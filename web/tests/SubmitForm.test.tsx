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
  onQueue: () => {},
  submitting: false,
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

describe("SubmitForm Queue button + Enter submit", () => {
  it("renders the Queue button", () => {
    render(<SubmitForm {...baseProps} />);
    expect(screen.getByRole("button", { name: /^Queue$/ })).toBeInTheDocument();
  });

  it("disables Queue when the URL is empty", () => {
    render(<SubmitForm {...baseProps} url="" />);
    const btn = screen.getByRole("button", { name: /^Queue$/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("disables Queue when the URL is whitespace-only", () => {
    render(<SubmitForm {...baseProps} url="   " />);
    const btn = screen.getByRole("button", { name: /^Queue$/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("disables Queue when the URL doesn't start with http(s)://", () => {
    render(<SubmitForm {...baseProps} url="ftp://example.com/a" />);
    const btn = screen.getByRole("button", { name: /^Queue$/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("enables Queue for a well-shaped https URL", () => {
    render(<SubmitForm {...baseProps} url="https://yt/x" />);
    const btn = screen.getByRole("button", { name: /^Queue$/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it("enables Queue for a well-shaped http URL", () => {
    render(<SubmitForm {...baseProps} url="http://yt/x" />);
    const btn = screen.getByRole("button", { name: /^Queue$/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it("disables Queue while submitting, even with a valid URL", () => {
    render(<SubmitForm {...baseProps} url="https://yt/x" submitting={true} />);
    const btn = screen.getByRole("button", { name: /…|Queue/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("renders the ellipsis label while submitting", () => {
    render(<SubmitForm {...baseProps} url="https://yt/x" submitting={true} />);
    // The button's accessible name is the ellipsis while a submit is in flight.
    expect(screen.queryByRole("button", { name: /^Queue$/ })).toBeNull();
    expect(screen.getByRole("button", { name: /…/ })).toBeInTheDocument();
  });

  it("fires onQueue when the Queue button is clicked", () => {
    const onQueue = vi.fn();
    render(<SubmitForm {...baseProps} url="https://yt/x" onQueue={onQueue} />);
    fireEvent.click(screen.getByRole("button", { name: /^Queue$/ }));
    expect(onQueue).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire onQueue when the button is clicked while disabled", () => {
    const onQueue = vi.fn();
    render(<SubmitForm {...baseProps} url="" onQueue={onQueue} />);
    // Disabled <button> click is a no-op at the DOM level; assert defensively.
    fireEvent.click(screen.getByRole("button", { name: /^Queue$/ }));
    expect(onQueue).not.toHaveBeenCalled();
  });

  it("fires onQueue when Enter is pressed in the URL input (form submit)", () => {
    const onQueue = vi.fn();
    render(<SubmitForm {...baseProps} url="https://yt/x" onQueue={onQueue} />);
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    // Hitting Enter in a form's only input triggers its submit. fireEvent
    // delivers the same event the browser would, including the submit
    // bubbling to the form.
    fireEvent.submit(input.closest("form")!);
    expect(onQueue).toHaveBeenCalledTimes(1);
  });

  it("does NOT fire onQueue on keystrokes other than Enter", () => {
    const onQueue = vi.fn();
    render(<SubmitForm {...baseProps} url="https://yt/x" onQueue={onQueue} />);
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.change(input, { target: { value: "https://yt/xy" } });
    fireEvent.keyDown(input, { key: "a" });
    expect(onQueue).not.toHaveBeenCalled();
  });

  it("does NOT fire onQueue on Enter when the URL is invalid", () => {
    const onQueue = vi.fn();
    render(<SubmitForm {...baseProps} url="not-a-url" onQueue={onQueue} />);
    const input = screen.getByPlaceholderText(/Paste a YouTube URL/i);
    fireEvent.submit(input.closest("form")!);
    expect(onQueue).not.toHaveBeenCalled();
  });
});
