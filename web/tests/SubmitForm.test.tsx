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
        audioOnly={false}
        onAudioOnlyChange={() => {}}
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
        audioOnly={false}
        onAudioOnlyChange={() => {}}
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
        audioOnly={false}
        onAudioOnlyChange={() => {}}
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
        audioOnly={false}
        onAudioOnlyChange={() => {}}
      />,
    );
    expect(screen.getByText(/your locale \+ EN/i)).toBeInTheDocument();
  });
});

describe("SubmitForm audio-only checkbox", () => {
  it("renders the audio-only checkbox with its label", () => {
    render(
      <SubmitForm
        url=""
        onUrlChange={() => {}}
        format="best"
        onFormatChange={() => {}}
        subtitles={false}
        onSubtitlesChange={() => {}}
        audioOnly={false}
        onAudioOnlyChange={() => {}}
      />,
    );
    const checkbox = screen.getByRole("checkbox", { name: /audio only/i });
    expect(checkbox).toBeInTheDocument();
    expect((checkbox as HTMLInputElement).checked).toBe(false);
    expect(screen.getByText(/MP3-style, no video/i)).toBeInTheDocument();
  });

  it("fires onAudioOnlyChange with the new value when toggled", async () => {
    const onChange = vi.fn();
    render(
      <SubmitForm
        url=""
        onUrlChange={() => {}}
        format="best"
        onFormatChange={() => {}}
        subtitles={false}
        onSubtitlesChange={() => {}}
        audioOnly={false}
        onAudioOnlyChange={onChange}
      />,
    );
    const checkbox = screen.getByRole("checkbox", { name: /audio only/i });
    await act(async () => {
      checkbox.click();
    });
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("disables the format dropdown when audioOnly is true", () => {
    render(
      <SubmitForm
        url=""
        onUrlChange={() => {}}
        format="best"
        onFormatChange={() => {}}
        subtitles={false}
        onSubtitlesChange={() => {}}
        audioOnly={true}
        onAudioOnlyChange={() => {}}
      />,
    );
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.disabled).toBe(true);
    expect(select.title).toMatch(/no effect/i);
  });

  it("leaves the format dropdown enabled when audioOnly is false", () => {
    render(
      <SubmitForm
        url=""
        onUrlChange={() => {}}
        format="best"
        onFormatChange={() => {}}
        subtitles={false}
        onSubtitlesChange={() => {}}
        audioOnly={false}
        onAudioOnlyChange={() => {}}
      />,
    );
    const select = screen.getByRole("combobox") as HTMLSelectElement;
    expect(select.disabled).toBe(false);
  });
});
