import { act, render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SubmitForm } from "../src/components/SubmitForm";

describe("SubmitForm subtitles checkbox", () => {
  it("renders an unchecked checkbox by default", () => {
    render(
      <SubmitForm
        url=""
        onUrlChange={() => {}}
        format="best"
        onFormatChange={() => {}}
        subtitles={false}
        onSubtitlesChange={() => {}}
      />,
    );
    const checkbox = screen.getByRole("checkbox", { name: /subtitles/i });
    expect(checkbox).toBeInTheDocument();
    expect((checkbox as HTMLInputElement).checked).toBe(false);
  });

  it("reflects the subtitles prop value", () => {
    render(
      <SubmitForm
        url=""
        onUrlChange={() => {}}
        format="best"
        onFormatChange={() => {}}
        subtitles={true}
        onSubtitlesChange={() => {}}
      />,
    );
    const checkbox = screen.getByRole("checkbox", { name: /subtitles/i });
    expect((checkbox as HTMLInputElement).checked).toBe(true);
  });

  it("fires the change handler when toggled", async () => {
    const onChange = vi.fn();
    render(
      <SubmitForm
        url=""
        onUrlChange={() => {}}
        format="best"
        onFormatChange={() => {}}
        subtitles={false}
        onSubtitlesChange={onChange}
      />,
    );
    const checkbox = screen.getByRole("checkbox", { name: /subtitles/i });
    await act(async () => {
      checkbox.click();
    });
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("shows a hint about the locale + EN default", () => {
    render(
      <SubmitForm
        url=""
        onUrlChange={() => {}}
        format="best"
        onFormatChange={() => {}}
        subtitles={false}
        onSubtitlesChange={() => {}}
      />,
    );
    expect(screen.getByText(/your locale \+ EN/i)).toBeInTheDocument();
  });
});
