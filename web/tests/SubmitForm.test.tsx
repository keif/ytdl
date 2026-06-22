import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SubmitForm } from "../src/components/SubmitForm";

// Shared defaults so each test only has to spell out what it cares about.
// Most tests below don't care about the per-paste override fields; supplying
// inert defaults keeps the prop surface ergonomic without losing TS coverage.
const baseProps = {
  url: "",
  onUrlChange: () => {},
  format: "best",
  onFormatChange: () => {},
  subtitles: false,
  onSubtitlesChange: () => {},
  audioOnly: false,
  onAudioOnlyChange: () => {},
  outputDir: "",
  onOutputDirChange: () => {},
  outputDirPlaceholder: "",
};

describe("SubmitForm subtitles checkbox", () => {
  it("renders an unchecked checkbox by default", () => {
    render(<SubmitForm {...baseProps} />);
    const checkbox = screen.getByRole("checkbox", { name: /subtitles/i });
    expect(checkbox).toBeInTheDocument();
    expect((checkbox as HTMLInputElement).checked).toBe(false);
  });

  it("reflects the subtitles prop value", () => {
    render(<SubmitForm {...baseProps} subtitles={true} />);
    const checkbox = screen.getByRole("checkbox", { name: /subtitles/i });
    expect((checkbox as HTMLInputElement).checked).toBe(true);
  });

  it("fires the change handler when toggled", async () => {
    const onChange = vi.fn();
    render(<SubmitForm {...baseProps} onSubtitlesChange={onChange} />);
    const checkbox = screen.getByRole("checkbox", { name: /subtitles/i });
    await act(async () => {
      checkbox.click();
    });
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("shows a hint about the locale + EN default", () => {
    render(<SubmitForm {...baseProps} />);
    expect(screen.getByText(/your locale \+ EN/i)).toBeInTheDocument();
  });
});

describe("SubmitForm audio-only checkbox", () => {
  it("renders the audio-only checkbox with its label", () => {
    render(<SubmitForm {...baseProps} />);
    const checkbox = screen.getByRole("checkbox", { name: /audio only/i });
    expect(checkbox).toBeInTheDocument();
    expect((checkbox as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(/MP3-style, no video/i)).toBeInTheDocument();
  });

  it("fires onAudioOnlyChange with the new value when toggled", async () => {
    const onChange = vi.fn();
    render(<SubmitForm {...baseProps} onAudioOnlyChange={onChange} />);
    const checkbox = screen.getByRole("checkbox", { name: /audio only/i });
    await act(async () => {
      checkbox.click();
    });
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("disables the format dropdown when audioOnly is true", () => {
    render(<SubmitForm {...baseProps} audioOnly={true} />);
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(select.title).toMatch(/no effect/i);
  });

  it("leaves the format dropdown enabled when audioOnly is false", () => {
    render(<SubmitForm {...baseProps} />);
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
  });
});

describe("SubmitForm advanced output_dir override", () => {
  it("renders an Advanced disclosure collapsed by default", () => {
    render(<SubmitForm {...baseProps} />);
    const summary = screen.getByText(/^Advanced$/);
    expect(summary).toBeInTheDocument();
    // <summary>'s parent is the <details> element.
    const details = summary.closest("details");
    expect(details).not.toBeNull();
    expect((details as HTMLDetailsElement).open).toBe(false);
  });

  it("exposes a 'Save to' text input inside the disclosure", () => {
    render(<SubmitForm {...baseProps} outputDirPlaceholder="/home/u/Videos/ytdl" />);
    const input = screen.getByLabelText(/Save to/i) as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.tagName).toBe("INPUT");
    expect(input.placeholder).toBe("/home/u/Videos/ytdl");
  });

  it("falls back to '(default)' placeholder when no server default is known yet", () => {
    render(<SubmitForm {...baseProps} outputDirPlaceholder="" />);
    const input = screen.getByLabelText(/Save to/i) as HTMLInputElement;
    expect(input.placeholder).toBe("(default)");
  });

  it("fires onOutputDirChange as the user types", () => {
    const onChange = vi.fn();
    render(<SubmitForm {...baseProps} onOutputDirChange={onChange} />);
    const input = screen.getByLabelText(/Save to/i) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "~/Music" } });
    expect(onChange).toHaveBeenCalledWith("~/Music");
  });

  it("reflects the outputDir prop value in the input", () => {
    render(<SubmitForm {...baseProps} outputDir="~/Music" />);
    const input = screen.getByLabelText(/Save to/i) as HTMLInputElement;
    expect(input.value).toBe("~/Music");
  });
});
