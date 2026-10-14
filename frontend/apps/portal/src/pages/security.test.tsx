import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { device, first, ME } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

/* The portal's side of M14a (ADR-0021): the password again before adding a TV app (G-31),
 * and the credits on a public About page. */

const SIGNED_IN = {
  "GET /api/v1/me": { body: ME },
  "GET /api/v1/me/subscription": { body: { subscription: null, pending: null } },
};

const CREATED = {
  device: device({ id: "d2", name: "Bedroom TV", xtream_username: "bedroom-tv" }),
  server_url: "http://tv.localhost",
  username: "bedroom-tv",
  password: "s3cret-pass-word",
};

async function addTvApp(user: ReturnType<typeof renderApp>["user"]): Promise<void> {
  await user.click(first(await screen.findAllByRole("button", { name: "Add TV app" })));
  const dialog = await screen.findByRole("dialog", { name: "Add a TV app" });
  await user.type(within(dialog).getByLabelText("Device name"), "Bedroom TV");
  await user.click(within(dialog).getByRole("button", { name: "Create login" }));
}

describe("confirm your password", () => {
  it("asks for the password before adding a TV app, then adds it", async () => {
    let posts = 0;
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/me/devices": { body: [] },
      "POST /api/v1/me/devices": () => {
        posts += 1;
        return posts === 1 ? problem(403, "STEP_UP_REQUIRED") : { status: 201, body: CREATED };
      },
      "POST /api/v1/auth/step-up": { body: { step_up_until: new Date().toISOString() } },
    });
    const { user } = renderApp("/account/devices");
    await addTvApp(user);
    const confirm = await screen.findByRole("dialog", { name: "Confirm your password" });
    await user.type(within(confirm).getByLabelText("Password"), "my-portal-password");
    await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("dialog", { name: "Your TV app login" })).toBeTruthy();
    expect(api.sent("POST", "/api/v1/auth/step-up")[0]?.body).toEqual({
      password: "my-portal-password",
    });
    expect(api.sent("POST", "/api/v1/me/devices")).toHaveLength(2);
  });

  it("says a wrong password in Arabic, and nothing is added when cancelled", async () => {
    const api = mockApi({
      ...SIGNED_IN,
      "GET /api/v1/me/devices": { body: [] },
      "POST /api/v1/me/devices": problem(403, "STEP_UP_REQUIRED"),
      "POST /api/v1/auth/step-up": problem(401, "INVALID_CREDENTIALS"),
    });
    const { user } = renderApp("/account/devices", { language: "ar" });
    await user.click(first(await screen.findAllByRole("button", { name: "إضافة تطبيق تلفاز" })));
    const dialog = await screen.findByRole("dialog", { name: "إضافة تطبيق تلفاز" });
    await user.type(within(dialog).getByLabelText("اسم الجهاز"), "غرفة النوم");
    await user.click(within(dialog).getByRole("button", { name: "أنشئ بيانات الدخول" }));
    const confirm = await screen.findByRole("dialog", { name: "أكّد كلمة المرور" });
    await user.type(within(confirm).getByLabelText("كلمة المرور"), "wrong");
    await user.click(within(confirm).getByRole("button", { name: "تأكيد" }));
    expect(await within(confirm).findByText("كلمة المرور غير صحيحة. حاول مجددًا.")).toBeTruthy();
    await user.click(within(confirm).getByRole("button", { name: "إلغاء" }));
    expect(await screen.findByText("لم يُنفَّذ: أكّد كلمة المرور للمتابعة.")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/me/devices")).toHaveLength(1);
  });
});

describe("about", () => {
  it("credits TMDB and DB-IP without signing in", async () => {
    mockApi({ "GET /api/v1/me": problem(401, "NOT_AUTHENTICATED") });
    renderApp("/about");
    expect(await screen.findByRole("heading", { level: 1, name: "About" })).toBeTruthy();
    expect(screen.getByRole("img", { name: /TMDB/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: /DB-IP/ })).toBeTruthy();
  });
});
