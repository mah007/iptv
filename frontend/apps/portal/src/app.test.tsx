import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "./app";

afterEach(cleanup);

describe("portal shell", () => {
  it("renders the home page and switches to Arabic right-to-left", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Your streaming home" })).toBeTruthy();
    expect(document.title).toBe("Smart IPTV");
    expect(document.documentElement.dataset.theme).toBe("dark");

    fireEvent.click(screen.getByRole("button", { name: /Change language/ }));

    expect(await screen.findByRole("heading", { name: "وجهتك للمشاهدة" })).toBeTruthy();
    expect(document.documentElement.dir).toBe("rtl");
    expect(document.documentElement.lang).toBe("ar");
  });
});
