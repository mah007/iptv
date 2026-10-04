"""The only tags and filters notification templates may use (`rendering.SandboxEngine`).

A whitelist taken from Django's built-in libraries. Left out on purpose:
- `load`, `include`, `extends`, `url`, `csrf_token`, `debug`: they reach other
  templates, code or process state;
- `safe` and `safeseq`: payload values (a customer's name) must stay escaped;
- `pprint`, `stringformat`, `floatformat`, `ljust`/`rjust`/`center`: they expose
  object internals or let a template allocate unbounded memory.
"""

from django.template import Library, defaultfilters, defaulttags

TAGS = (
    "autoescape",
    "comment",
    "firstof",
    "for",
    "if",
    "spaceless",
    "templatetag",
    "verbatim",
    "with",
)
FILTERS = (
    "capfirst",
    "default",
    "default_if_none",
    "escape",
    "first",
    "force_escape",
    "join",
    "last",
    "length",
    "linebreaks",
    "linebreaksbr",
    "lower",
    "pluralize",
    "striptags",
    "title",
    "truncatechars",
    "truncatewords",
    "upper",
    "urlencode",
    "urlize",
    "yesno",
)

register = Library()
for _tag in TAGS:
    register.tags[_tag] = defaulttags.register.tags[_tag]
for _filter in FILTERS:
    register.filters[_filter] = defaultfilters.register.filters[_filter]
