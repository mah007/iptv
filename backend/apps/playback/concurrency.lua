--[[
Stream slots and live sessions in redis-state (SPEC 7.4), driven by
apps/playback/concurrency.py. Every mode runs atomically.

  conc:<user>     zset: one member "<session>:<device>" per stream slot, scored by its
                  last activity (Unix seconds). A member idle longer than the window
                  no longer holds a slot.
  kick:<session>  why the session was stopped (a reason string, with a TTL)
  sess:<session>  hash: the live session record (written by start_playback)
  sess:index      zset of live session ids, scored by their last activity
  ent:<user>      the entitlement JSON (apps/playback/entitlements.py)

A device plays one stream at a time: starting another title on it ends the
device's previous session ("replaced"). redis-state is a single instance (SPEC 3),
so modes may touch keys named in session records; this would not hold on a cluster.

ARGV[1] picks the mode:
  start      KEYS conc, kick, index.
             ARGV member, now, window, max_streams, policy, conc_ttl, kick_ttl,
             kick_prefix, sess_prefix
             -> {"refreshed"} | {"rejected"}
                | {"added", session, reason, seen, bytes, ...} (one quad per eviction)
  heartbeat  KEYS sess, kick, index.
             ARGV session, now, window, conc_ttl, conc_prefix, ent_prefix, ip, edge, bytes
             -> {"allow", "refreshed" | "readded"} | {"deny", reason}
  finish     KEYS sess, kick, index.  ARGV session, reason, kick_ttl, conc_prefix
             -> {seen, bytes} ("" when the session had no record)
  reap       KEYS sess, index.  ARGV session, now, conc_prefix, default_idle
             -> {"live"} | {"gone"} | {"reaped", row, seen, bytes}
]]

local function session_of(member)
  return string.sub(member, 1, 32)
end

local function device_of(member)
  return string.sub(member, 34)
end

local function prune(conc, now, window)
  redis.call("ZREMRANGEBYSCORE", conc, "-inf", "(" .. tostring(now - window))
end

local function start()
  local conc, kick, index = KEYS[1], KEYS[2], KEYS[3]
  local member, now_s = ARGV[2], ARGV[3]
  local now, window = tonumber(now_s), tonumber(ARGV[4])
  local max_streams, policy = tonumber(ARGV[5]), ARGV[6]
  local conc_ttl, kick_ttl = tonumber(ARGV[7]), tonumber(ARGV[8])
  local kick_prefix, sess_prefix = ARGV[9], ARGV[10]

  prune(conc, now, window)
  if redis.call("ZSCORE", conc, member) then
    redis.call("ZADD", conc, now_s, member)
    redis.call("EXPIRE", conc, conc_ttl)
    return {"refreshed"}
  end

  local device = device_of(member)
  local evict, others = {}, {}
  for _, held in ipairs(redis.call("ZRANGE", conc, 0, -1)) do -- oldest first
    if device_of(held) == device then
      evict[#evict + 1] = {held, "replaced"}
    else
      others[#others + 1] = held
    end
  end
  local excess = #others - max_streams + 1
  if excess > 0 then
    if policy ~= "kick_oldest" then
      return {"rejected"}
    end
    for i = 1, excess do
      evict[#evict + 1] = {others[i], "stream_limit"}
    end
  end

  local reply = {"added"}
  for _, pair in ipairs(evict) do
    local session = session_of(pair[1])
    local sess = sess_prefix .. session
    local record = redis.call("HMGET", sess, "seen", "bytes")
    redis.call("ZREM", conc, pair[1])
    redis.call("SET", kick_prefix .. session, pair[2], "EX", kick_ttl)
    redis.call("DEL", sess)
    redis.call("ZREM", index, session)
    reply[#reply + 1] = session
    reply[#reply + 1] = pair[2]
    reply[#reply + 1] = record[1] or ""
    reply[#reply + 1] = record[2] or ""
  end
  redis.call("ZADD", conc, now_s, member)
  redis.call("EXPIRE", conc, conc_ttl)
  -- A new start supersedes an earlier stop of the same session.
  redis.call("DEL", kick)
  return reply
end

local function heartbeat()
  local sess, kick, index = KEYS[1], KEYS[2], KEYS[3]
  local session, now_s = ARGV[2], ARGV[3]
  local now, window = tonumber(now_s), tonumber(ARGV[4])
  local conc_ttl, conc_prefix, ent_prefix = tonumber(ARGV[5]), ARGV[6], ARGV[7]
  local ip, edge, bytes = ARGV[8], ARGV[9], tonumber(ARGV[10])

  local reason = redis.call("GET", kick)
  if reason then
    return {"deny", reason}
  end
  local record = redis.call("HMGET", sess, "user", "device", "max_streams")
  local user, device = record[1], record[2]
  if not user or not device then
    return {"deny", "session_ended"}
  end
  local max_streams = tonumber(record[3]) or 1
  local raw = redis.call("GET", ent_prefix .. user)
  if raw then
    local ok, ent = pcall(cjson.decode, raw)
    if ok and type(ent) == "table" then
      if ent["status"] ~= "active" then
        return {"deny", "access_ended"}
      end
      if tonumber(ent["max_streams"]) then
        max_streams = tonumber(ent["max_streams"])
      end
    end
  end

  local conc = conc_prefix .. user
  local member = session .. ":" .. device
  prune(conc, now, window)
  local status = "refreshed"
  if not redis.call("ZSCORE", conc, member) then
    -- The slot lapsed (one long response makes no requests): take it again if the
    -- device has not moved on and the limit allows. A heartbeat never evicts.
    local held = 0
    for _, other in ipairs(redis.call("ZRANGE", conc, 0, -1)) do
      if device_of(other) == device then
        return {"deny", "replaced"}
      end
      held = held + 1
    end
    if held >= max_streams then
      return {"deny", "stream_limit"}
    end
    status = "readded"
  end
  redis.call("ZADD", conc, now_s, member)
  redis.call("EXPIRE", conc, conc_ttl)
  redis.call("HSET", sess, "seen", now_s)
  if ip ~= "" then
    redis.call("HSET", sess, "ip", ip)
  end
  if edge ~= "" then
    redis.call("HSET", sess, "edge", edge)
  end
  if bytes and bytes > 0 then
    redis.call("HINCRBY", sess, "bytes", ARGV[10]) -- the decimal string: exact
  end
  redis.call("ZADD", index, now_s, session)
  return {"allow", status}
end

local function finish()
  local sess, kick, index = KEYS[1], KEYS[2], KEYS[3]
  local session, reason = ARGV[2], ARGV[3]
  local kick_ttl, conc_prefix = tonumber(ARGV[4]), ARGV[5]
  redis.call("SET", kick, reason, "EX", kick_ttl)
  local record = redis.call("HMGET", sess, "user", "device", "seen", "bytes")
  if record[1] and record[2] then
    redis.call("ZREM", conc_prefix .. record[1], session .. ":" .. record[2])
  end
  redis.call("DEL", sess)
  redis.call("ZREM", index, session)
  return {record[3] or "", record[4] or ""}
end

local function reap()
  local sess, index = KEYS[1], KEYS[2]
  local session, now, conc_prefix = ARGV[2], tonumber(ARGV[3]), ARGV[4]
  local default_idle = tonumber(ARGV[5])
  local record = redis.call("HMGET", sess, "seen", "idle", "user", "device", "row", "bytes")
  local seen = tonumber(record[1])
  if not seen then
    redis.call("ZREM", index, session)
    return {"gone"}
  end
  if seen > now - (tonumber(record[2]) or default_idle) then
    return {"live"}
  end
  if record[3] and record[4] then
    redis.call("ZREM", conc_prefix .. record[3], session .. ":" .. record[4])
  end
  redis.call("DEL", sess)
  redis.call("ZREM", index, session)
  return {"reaped", record[5] or "", record[1], record[6] or ""}
end

local mode = ARGV[1]
if mode == "start" then
  return start()
elseif mode == "heartbeat" then
  return heartbeat()
elseif mode == "finish" then
  return finish()
elseif mode == "reap" then
  return reap()
end
return redis.error_reply("unknown mode")
