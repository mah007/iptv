import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { credential, customerDetail, device, page, signedIn } from "../test/fixtures";
import { mockApi, problem } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const DETAIL = "/api/v1/admin/customers/cust-1";

describe("customer detail", () => {
  it("shows the access profile and the profile on the overview", async () => {
    mockApi(signedIn({ [`GET ${DETAIL}`]: { body: customerDetail() } }));
    renderApp("/customers/cust-1");
    expect(await screen.findByRole("heading", { name: /Sara Ahmed/ })).toBeTruthy();
    const access = screen.getByRole("heading", { name: "Access profile" }).closest("section");
    expect(access?.textContent).toContain("Full HD 1080p");
    expect(access?.textContent).toContain("Refuse new streams");
    expect(access?.textContent).toContain("All categories");
    expect(access?.textContent).toContain("1 / 2");
    expect(screen.getByText("+966501234567")).toBeTruthy();
    expect(document.title).toBe("Sara Ahmed · Smart IPTV Admin");
  });

  it("resets a device password and shows the new one once", async () => {
    const api = mockApi(
      signedIn({
        [`GET ${DETAIL}`]: { body: customerDetail() },
        "POST /api/v1/admin/devices/dev-1/reset-credentials": {
          body: credential({ password: "Nw4pQ8zLm2xV7cRt" }),
        },
      }),
    );
    const { user } = renderApp("/customers/cust-1?tab=devices");
    await user.click(await screen.findByRole("button", { name: "Actions for Living room TV" }));
    await user.click(await screen.findByRole("menuitem", { name: "Reset password" }));
    const confirm = await screen.findByRole("dialog", {
      name: "Reset the password of Living room TV?",
    });
    expect(within(confirm).getByRole("radio", { name: /Generate automatically/ })).toBeTruthy();
    await user.click(within(confirm).getByRole("button", { name: "Reset password" }));

    const issued = await screen.findByRole("dialog", { name: "New password issued" });
    expect(api.sent("POST", "/api/v1/admin/devices/dev-1/reset-credentials")).toHaveLength(1);
    expect(api.sent("POST", "/api/v1/admin/devices/dev-1/reset-credentials")[0]?.body).toEqual({});
    expect(within(issued).getByDisplayValue("sar-q7k2pa")).toBeTruthy();
    expect(within(issued).getByRole("img", { name: /QR code/ })).toBeTruthy();
    await user.click(within(issued).getByRole("button", { name: "Reveal" }));
    expect(within(issued).getByText("Nw4pQ8zLm2xV7cRt")).toBeTruthy();
    await user.click(within(issued).getByRole("button", { name: "Done" }));
    expect(screen.queryByText("Nw4pQ8zLm2xV7cRt")).toBeNull();
  });

  it("resets to a password the admin sets, renaming the login, with the API's field errors inline", async () => {
    let attempts = 0;
    const api = mockApi(
      signedIn({
        [`GET ${DETAIL}`]: { body: customerDetail() },
        "POST /api/v1/admin/devices/dev-1/reset-credentials": () => {
          attempts += 1;
          return attempts === 1
            ? problem(400, "VALIDATION_ERROR", { username: ["This username is already taken."] })
            : { body: credential({ username: "sara-tv", password: "Tv.pass~123" }) };
        },
      }),
    );
    const { user } = renderApp("/customers/cust-1?tab=devices");
    await user.click(await screen.findByRole("button", { name: "Actions for Living room TV" }));
    await user.click(await screen.findByRole("menuitem", { name: "Reset password" }));
    const dialog = await screen.findByRole("dialog", {
      name: "Reset the password of Living room TV?",
    });
    await user.click(within(dialog).getByRole("radio", { name: /Set manually/ }));
    const username = within(dialog).getByLabelText("Username");
    expect((username as HTMLInputElement).value).toBe("sar-q7k2pa");

    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));
    expect(await within(dialog).findByText("Use 8 to 64 characters.")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/devices/dev-1/reset-credentials")).toHaveLength(0);

    await user.clear(username);
    await user.type(username, "sara-tv");
    await user.type(within(dialog).getByLabelText("Password"), "Tv.pass~123");
    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));
    expect(await within(dialog).findByText("This username is already taken.")).toBeTruthy();

    await user.click(within(dialog).getByRole("button", { name: "Reset password" }));
    expect(await screen.findByRole("dialog", { name: "New password issued" })).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/devices/dev-1/reset-credentials").at(-1)?.body).toEqual({
      username: "sara-tv",
      password: "Tv.pass~123",
    });
  });

  it("adds a device with a login the admin chose, and a generated password", async () => {
    const api = mockApi(
      signedIn({
        [`GET ${DETAIL}`]: { body: customerDetail() },
        [`POST ${DETAIL}/devices`]: { status: 201, body: credential() },
      }),
    );
    const { user } = renderApp("/customers/cust-1?tab=devices");
    await user.click(await screen.findByRole("button", { name: "Add device" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a device" });
    await user.click(within(dialog).getByRole("radio", { name: /Set manually/ }));
    await user.type(within(dialog).getByLabelText("Username"), "sara-phone");
    const password = within(dialog).getByLabelText("Password");
    expect(password.getAttribute("type")).toBe("password");
    await user.click(within(dialog).getByRole("button", { name: "Generate" }));
    // The generated password is shown so the admin can read it out.
    expect(password.getAttribute("type")).toBe("text");
    const generated = (password as HTMLInputElement).value;
    expect(generated).toMatch(/^[2-9a-hjkmnp-z]{16}$/u);
    await user.click(within(dialog).getByRole("button", { name: "Create login" }));
    expect(await screen.findByRole("dialog", { name: "Device login created" })).toBeTruthy();
    expect(api.sent("POST", `${DETAIL}/devices`)[0]?.body).toEqual({
      app_hint: "other",
      username: "sara-phone",
      password: generated,
    });
  });

  it("adds a device until the access profile's limit, then disables adding", async () => {
    const api = mockApi(
      signedIn({
        [`GET ${DETAIL}`]: { body: customerDetail() },
        [`POST ${DETAIL}/devices`]: { status: 201, body: credential() },
      }),
    );
    const { user } = renderApp("/customers/cust-1?tab=devices");
    await user.click(await screen.findByRole("button", { name: "Add device" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a device" });
    await user.type(within(dialog).getByLabelText("Device name"), "Phone");
    await user.click(within(dialog).getByRole("button", { name: "Create login" }));
    expect(await screen.findByRole("dialog", { name: "Device login created" })).toBeTruthy();
    expect(api.sent("POST", `${DETAIL}/devices`)[0]?.body).toEqual({
      name: "Phone",
      app_hint: "other",
    });
  });

  it("can't add a device past the limit", async () => {
    mockApi(
      signedIn({
        [`GET ${DETAIL}`]: {
          body: customerDetail({ devices: [device(), device({ id: "dev-2", name: "Phone" })] }),
        },
      }),
    );
    renderApp("/customers/cust-1?tab=devices");
    expect(await screen.findByText("2 of 2 devices in use")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add device" }).hasAttribute("disabled")).toBe(true);
  });

  it("blocks a device with a reason and keeps the dialog open when it fails", async () => {
    let attempts = 0;
    const api = mockApi(
      signedIn({
        [`GET ${DETAIL}`]: { body: customerDetail() },
        "POST /api/v1/admin/devices/dev-1/block": () => {
          attempts += 1;
          return attempts === 1
            ? problem(500, "INTERNAL_ERROR")
            : { body: device({ blocked: true }) };
        },
      }),
    );
    const { user } = renderApp("/customers/cust-1?tab=devices");
    await user.click(await screen.findByRole("button", { name: "Actions for Living room TV" }));
    await user.click(await screen.findByRole("menuitem", { name: "Block" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Block Living room TV?" });
    await user.type(within(dialog).getByLabelText("Reason (optional)"), "Shared");
    await user.click(within(dialog).getByRole("button", { name: "Block device" }));
    expect(await screen.findByText("Something went wrong. Try again.")).toBeTruthy();
    expect(screen.getByRole("alertdialog", { name: "Block Living room TV?" })).toBeTruthy();

    await user.click(within(dialog).getByRole("button", { name: "Block device" }));
    expect(await screen.findByText("Device blocked")).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/devices/dev-1/block").at(-1)?.body).toEqual({
      reason: "Shared",
    });
  });

  it("suspends the customer after confirmation", async () => {
    const api = mockApi(
      signedIn({
        [`GET ${DETAIL}`]: { body: customerDetail() },
        [`POST ${DETAIL}/suspend`]: { body: customerDetail({ status: "suspended" }) },
      }),
    );
    const { user } = renderApp("/customers/cust-1");
    await user.click(await screen.findByRole("button", { name: "Suspend" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Suspend Sara Ahmed?" });
    await user.click(within(dialog).getByRole("button", { name: "Suspend customer" }));
    expect(await screen.findByRole("button", { name: "Reactivate" })).toBeTruthy();
    expect(api.sent("POST", `${DETAIL}/suspend`)[0]?.body).toEqual({});
  });

  it("sends only the changed fields of the access profile", async () => {
    const api = mockApi(
      signedIn({
        [`GET ${DETAIL}`]: { body: customerDetail() },
        "GET /api/v1/admin/categories": { body: page([]) },
        [`PATCH ${DETAIL}/access`]: { body: customerDetail().access },
      }),
    );
    const { user } = renderApp("/customers/cust-1");
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const sheet = await screen.findByRole("dialog", { name: "Edit access profile" });
    await user.clear(within(sheet).getByLabelText("Devices"));
    await user.type(within(sheet).getByLabelText("Devices"), "3");
    await user.click(within(sheet).getByRole("radio", { name: "4K 2160p" }));
    await user.click(within(sheet).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Access profile saved")).toBeTruthy();
    expect(api.sent("PATCH", `${DETAIL}/access`)[0]?.body).toEqual({
      max_devices: 3,
      max_quality: 2160,
    });
  });

  it("creates a portal password link and shows it once when it wasn't emailed", async () => {
    const api = mockApi(
      signedIn({
        [`GET ${DETAIL}`]: { body: customerDetail({ email: "" }) },
        [`POST ${DETAIL}/password-invite`]: {
          body: {
            url: "http://app.localhost/set-password#one-time-token",
            expires_at: new Date(Date.now() + 7 * 86_400_000).toISOString(),
            emailed: false,
          },
        },
      }),
    );
    const { user } = renderApp("/customers/cust-1");
    await user.click(await screen.findByRole("button", { name: "Portal password link" }));
    const confirm = await screen.findByRole("alertdialog", {
      name: "Send Sara Ahmed a password link?",
    });
    expect(confirm.textContent).toContain("The customer has no email address.");
    await user.click(within(confirm).getByRole("button", { name: "Create link" }));
    const shown = await screen.findByRole("dialog", { name: "Password link" });
    expect(shown.textContent).toContain("The link was not emailed.");
    expect(within(shown).getByRole("textbox", { name: "Single-use link" })).toHaveProperty(
      "value",
      "http://app.localhost/set-password#one-time-token",
    );
    expect(api.sent("POST", `${DETAIL}/password-invite`)).toHaveLength(1);
  });

  it("shows the watch history, newest first, with how far each title got", async () => {
    mockApi(
      signedIn({
        [`GET ${DETAIL}`]: { body: customerDetail() },
        [`GET ${DETAIL}/history`]: {
          body: page([
            {
              id: "wp-1",
              title: {
                kind: "series",
                id: "series-1",
                title: "Breaking Bad",
                title_ar: "بريكنج باد",
                season: 1,
                episode: 2,
                episode_title: "Cat's in the Bag",
              },
              position_ms: 1_200_000,
              duration_ms: 2_400_000,
              completed: false,
              updated_at: new Date().toISOString(),
            },
          ]),
        },
      }),
    );
    renderApp("/customers/cust-1?tab=history");
    const link = await screen.findByRole("link", { name: "Breaking Bad" });
    expect(link.getAttribute("href")).toBe("/series/series-1");
    expect(screen.getByText("S1 E2")).toBeTruthy();
    expect(screen.getByText("20:00 of 40:00")).toBeTruthy();
  });

  it("says when the customer doesn't exist", async () => {
    mockApi(signedIn({ [`GET ${DETAIL}`]: problem(404, "NOT_FOUND") }));
    renderApp("/customers/cust-1");
    expect(await screen.findByText("Not found")).toBeTruthy();
  });
});
