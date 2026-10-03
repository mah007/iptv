import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "./app";

afterEach(cleanup);

describe("admin shell", () => {
  it("renders the home page and switches to Arabic right-to-left", async () => {
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Welcome to Smart IPTV" })).toBeTruthy();
    expect(document.title).toBe("Smart IPTV Admin");

    fireEvent.click(screen.getByRole("button", { name: "Change language" }));

    expect(await screen.findByRole("heading", { name: "مرحبًا بك في Smart IPTV" })).toBeTruthy();
    expect(document.documentElement.dir).toBe("rtl");
    expect(document.documentElement.lang).toBe("ar");
  });
});
