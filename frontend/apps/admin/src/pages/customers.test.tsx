import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { credential, customerDetail, customerSummary, page, signedIn } from "../test/fixtures";
import { mockApi, problem, type MockRequest } from "../test/mock-api";
import { renderApp } from "../test/render-app";

const CATEGORIES = { "GET /api/v1/admin/categories": { body: page([]) } };

describe("customers list", () => {
  it("lists customers with their access, expiry and devices", async () => {
    mockApi(
      signedIn({
        "GET /api/v1/admin/customers": {
          body: page([
            customerSummary(),
            customerSummary({
              id: "cust-2",
              name: "Omar Ali",
              access_status: "expired",
              expires_at: "2026-01-01T00:00:00Z",
              device_count: 2,
            }),
          ]),
        },
      }),
    );
    renderApp("/customers");
    const table = await screen.findByRole("table", { name: "Customers" });
    const sara = (await within(table).findByRole("link", { name: "Sara Ahmed" })).closest("tr");
    expect(sara?.textContent).toContain("Active");
    expect(sara?.textContent).toContain("1 / 2");
    const omar = within(table).getByRole("link", { name: "Omar Ali" }).closest("tr");
    expect(omar?.textContent).toContain("Expired");
    expect(screen.getByText("1–2 of 2")).toBeTruthy();
  });

  it("keeps filters, search and sorting in the URL and sends them to the API", async () => {
    const api = mockApi(signedIn());
    const { user, router } = renderApp("/customers?access=expired");
    const table = await screen.findByRole("table", { name: "Customers" });
    expect(api.sent("GET", "/api/v1/admin/customers").at(-1)?.query.get("access_status")).toBe(
      "expired",
    );
    expect(screen.getByText("Access: Expired")).toBeTruthy();

    await user.type(screen.getByRole("searchbox"), "sara");
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ q: "sara", access: "expired" });
    });
    await waitFor(() => {
      expect(api.sent("GET", "/api/v1/admin/customers").at(-1)?.query.get("search")).toBe("sara");
    });

    await user.click(within(table).getByRole("button", { name: /^Expires/ }));
    await waitFor(() => {
      expect(router.state.location.search).toMatchObject({ ordering: "expires_at" });
    });
    expect(api.sent("GET", "/api/v1/admin/customers").at(-1)?.query.get("ordering")).toBe(
      "expires_at",
    );
  });
});

describe("create-customer wizard", () => {
  function createRoutes(onCreate: (request: MockRequest) => ReturnType<typeof problem> | object) {
    return signedIn({
      ...CATEGORIES,
      "POST /api/v1/admin/customers": (request: MockRequest) => onCreate(request),
    });
  }

  it("creates the customer and shows the first credential once, with a QR code", async () => {
    const api = mockApi(
      createRoutes(() => ({
        status: 201,
        body: { customer: customerDetail({ name: "Sara Ahmed" }), credential: credential() },
      })),
    );
    const { user } = renderApp("/customers?new=true");
    const sheet = await screen.findByRole("dialog", { name: "New customer" });

    await user.click(within(sheet).getByRole("button", { name: "Next" }));
    expect(await within(sheet).findByText("Enter the customer's name.")).toBeTruthy();

    await user.type(within(sheet).getByLabelText("Full name"), "Sara Ahmed");
    await user.type(within(sheet).getByLabelText("Phone"), "050 123 4567");
    await user.click(within(sheet).getByRole("button", { name: "Next" }));

    expect(await within(sheet).findByRole("radiogroup", { name: "Access length" })).toBeTruthy();
    await user.click(within(sheet).getByRole("radio", { name: "3 months" }));
    await user.clear(within(sheet).getByLabelText("Streams at once"));
    await user.type(within(sheet).getByLabelText("Streams at once"), "2");
    await user.click(within(sheet).getByRole("button", { name: "Next" }));

    await user.type(await within(sheet).findByLabelText("Device name"), "Living room TV");
    await user.click(within(sheet).getByRole("button", { name: "Create customer" }));

    const done = await screen.findByRole("dialog", { name: "Customer created" });
    const body = api.sent("POST", "/api/v1/admin/customers")[0]?.body as Record<string, unknown>;
    expect(body).toMatchObject({
      name: "Sara Ahmed",
      phone: "0501234567",
      locale: "en",
      timezone: "Asia/Riyadh",
      access: {
        max_streams: 2,
        max_devices: 2,
        max_quality: 1080,
        concurrency_policy: "reject",
        allow_movies: true,
        category_ids: [],
      },
      device: { name: "Living room TV", app_hint: "other" },
    });
    // Three months from today, at the end of that day in Riyadh (20:59:59 UTC).
    expect((body.access as { expires_at: string }).expires_at).toMatch(/T20:59:59\.000Z$/u);

    expect(within(done).getByDisplayValue("https://tv.example.com")).toBeTruthy();
    expect(within(done).getByDisplayValue("sar-q7k2pa")).toBeTruthy();
    expect(within(done).queryByText("Kx7mPq2vRt9wZb4n")).toBeNull();
    const qr = within(done).getByRole("img", {
      name: "QR code with the server, username and password",
    });
    expect(qr).toBeTruthy();

    await user.click(within(done).getByRole("button", { name: "Reveal" }));
    expect(within(done).getByText("Kx7mPq2vRt9wZb4n")).toBeTruthy();
    await user.click(within(done).getByRole("button", { name: "Hide" }));
    expect(within(done).queryByText("Kx7mPq2vRt9wZb4n")).toBeNull();
    expect(within(done).queryByRole("img", { name: /QR code/ })).toBeNull();
    expect(within(done).getByText("Hidden with the password")).toBeTruthy();
    expect(within(done).getByRole("link", { name: "Open customer" }).getAttribute("href")).toBe(
      "/customers/cust-1",
    );
  });

  it("takes the admin back to the step whose field the API rejected", async () => {
    mockApi(
      createRoutes(() =>
        problem(400, "VALIDATION_ERROR", { email: ["A customer with this email already exists."] }),
      ),
    );
    const { user } = renderApp("/customers?new=true");
    const sheet = await screen.findByRole("dialog", { name: "New customer" });
    await user.type(within(sheet).getByLabelText("Full name"), "Sara Ahmed");
    await user.type(within(sheet).getByLabelText("Email"), "sara@example.com");
    await user.click(within(sheet).getByRole("button", { name: "Next" }));
    await user.click(await within(sheet).findByRole("button", { name: "Next" }));
    await user.click(await within(sheet).findByRole("button", { name: "Create customer" }));

    expect(
      await within(sheet).findByText("A customer with this email already exists."),
    ).toBeTruthy();
    expect(within(sheet).getByLabelText("Full name")).toBeTruthy();
  });

  it("sends the account username and first device login the admin chose", async () => {
    const api = mockApi(
      createRoutes((request) => {
        const body = request.body as { device?: { username?: string } };
        return body.device?.username === "taken"
          ? problem(400, "VALIDATION_ERROR", {
              "device.username": ["This username is already taken."],
            })
          : { status: 201, body: { customer: customerDetail(), credential: credential() } };
      }),
    );
    const { user } = renderApp("/customers?new=true");
    const sheet = await screen.findByRole("dialog", { name: "New customer" });
    await user.type(within(sheet).getByLabelText("Full name"), "Sara Ahmed");
    await user.type(within(sheet).getByLabelText("Account username (optional)"), "a b");
    await user.click(within(sheet).getByRole("button", { name: "Next" }));
    expect(
      await within(sheet).findByText("Use 3 to 150 letters, digits and . _ @ + -"),
    ).toBeTruthy();
    await user.clear(within(sheet).getByLabelText("Account username (optional)"));
    await user.type(within(sheet).getByLabelText("Account username (optional)"), "sara.ahmed");
    await user.click(within(sheet).getByRole("button", { name: "Next" }));
    await user.click(await within(sheet).findByRole("button", { name: "Next" }));

    await user.click(await within(sheet).findByRole("radio", { name: /Set manually/ }));
    await user.type(within(sheet).getByLabelText("Username"), "taken");
    await user.type(within(sheet).getByLabelText("Password"), "Tv.pass~123");
    await user.click(within(sheet).getByRole("button", { name: "Create customer" }));
    expect(await within(sheet).findByText("This username is already taken.")).toBeTruthy();

    await user.clear(within(sheet).getByLabelText("Username"));
    await user.type(within(sheet).getByLabelText("Username"), "sara-tv");
    await user.click(within(sheet).getByRole("button", { name: "Create customer" }));
    expect(await screen.findByRole("dialog", { name: "Customer created" })).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/customers").at(-1)?.body).toMatchObject({
      username: "sara.ahmed",
      device: { app_hint: "other", username: "sara-tv", password: "Tv.pass~123" },
    });
  });

  it("can create the customer without a device credential", async () => {
    const api = mockApi(
      createRoutes(() => ({ status: 201, body: { customer: customerDetail(), credential: null } })),
    );
    const { user } = renderApp("/customers?new=true");
    const sheet = await screen.findByRole("dialog", { name: "New customer" });
    await user.type(within(sheet).getByLabelText("Full name"), "Sara Ahmed");
    await user.click(within(sheet).getByRole("button", { name: "Next" }));
    await user.click(await within(sheet).findByRole("radio", { name: "No expiry" }));
    await user.click(within(sheet).getByRole("button", { name: "Next" }));
    await user.click(
      await within(sheet).findByRole("switch", { name: "Create the first device login now" }),
    );
    await user.click(within(sheet).getByRole("button", { name: "Create customer" }));

    const done = await screen.findByRole("dialog", { name: "Customer created" });
    expect(within(done).getByText(/No device login was created/u)).toBeTruthy();
    expect(api.sent("POST", "/api/v1/admin/customers")[0]?.body).toMatchObject({
      access: { expires_at: null },
      device: null,
    });
  });
});
