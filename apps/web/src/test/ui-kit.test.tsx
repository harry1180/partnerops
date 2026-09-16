import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StatusPill, MoneyCell, Pagination, Button } from "@cloudpartnerops/ui";

describe("ui kit", () => {
  it("maps invoice lifecycle states to tones", () => {
    const { container } = render(<StatusPill status="under_review" />);
    expect(container.textContent).toMatch(/under review/i);
    expect(container.querySelector("span")?.className).toContain("amber"); // warning tone
  });

  it("renders Decimal-string money without float drift", () => {
    const { container } = render(<MoneyCell value="1234567.895" currency="USD" />);
    // Intl display rounds to 2dp: 1,234,567.90 — no float artifacts
    expect(container.textContent).toBe("$1,234,567.90");
  });

  it("marks negative amounts", () => {
    const { container } = render(<MoneyCell value="-12.34" />);
    expect(container.textContent).toContain("-$12.34");
    expect(container.querySelector("span")?.className).toContain("text-negative");
  });

  it("pagination calls onPage with bounded values", async () => {
    const onPage = vi.fn();
    render(<Pagination page={3} pageSize={25} total={200} onPage={onPage} />);
    expect(screen.getByText(/51–75 of 200/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /previous/i }));
    expect(onPage).toHaveBeenCalledWith(2);
    await userEvent.click(screen.getByRole("button", { name: /next/i }));
    expect(onPage).toHaveBeenCalledWith(4);
  });

  it("buttons are real controls with handlers", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Run pricing</Button>);
    await userEvent.click(screen.getByRole("button", { name: /run pricing/i }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
