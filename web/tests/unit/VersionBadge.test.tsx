import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import VersionBadge, { VersionBadgeView, fmtBuiltAt } from "../../src/components/VersionBadge";
import type { BuildInfo } from "../../src/api";

const RELEASE: BuildInfo = { version: "0.1.3", commit: "473b4fa", built_at: "2026-10-10T19:30:00Z", channel: "release" };
const PREVIEW: BuildInfo = { version: "0.1.4-preview", commit: "abc1234", built_at: "2026-10-11T08:00:00Z", channel: "preview" };
const DEV: BuildInfo = { version: "0.1.2+dev", commit: "abc1234", built_at: null, channel: "dev" };

describe("VersionBadgeView", () => {
  it("shows a release as version and commit, with an accessible label and no accent outline", () => {
    render(<VersionBadgeView info={RELEASE} />);
    const badge = screen.getByRole("button", { name: "Forge version 0.1.3, release, commit 473b4fa" });
    expect(badge).toHaveTextContent("v0.1.3 · 473b4fa");
    expect(badge).toHaveAttribute("data-channel", "release");
    expect(badge.className).not.toContain("ring-1");
    expect(badge.className).toContain("font-mono");
  });

  it("outlines a preview in the accent color", () => {
    render(<VersionBadgeView info={PREVIEW} />);
    const badge = screen.getByTestId("version-badge");
    expect(badge).toHaveTextContent("v0.1.4-preview · abc1234");
    expect(badge.className).toContain("ring-accent-300");
  });

  it("labels a dev server as dev", () => {
    render(<VersionBadgeView info={DEV} />);
    expect(screen.getByTestId("version-badge")).toHaveTextContent(/^dev · abc1234$/);
    expect(screen.getByRole("button", { name: /Forge version 0\.1\.2\+dev, dev, commit abc1234/ })).toBeInTheDocument();
  });

  it("shows details and a What's new link on hover for releases", async () => {
    const user = userEvent.setup();
    render(<VersionBadgeView info={RELEASE} />);
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.hover(screen.getByTestId("version-badge"));
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("Version0.1.3");
    expect(tip).toHaveTextContent("Channelrelease");
    expect(tip).toHaveTextContent("Commit473b4fa");
    expect(tip).toHaveTextContent(`Built${fmtBuiltAt(RELEASE.built_at)}`);
    expect(fmtBuiltAt(RELEASE.built_at)).not.toBe(RELEASE.built_at); // local date and time, not the raw ISO text
    const link = screen.getByRole("link", { name: "What's new" });
    expect(link).toHaveAttribute("href", "https://github.com/utkanuygur/forge/releases/tag/v0.1.3");
    expect(screen.getByTestId("version-badge")).toHaveAttribute("aria-describedby", tip.id);
    await user.unhover(screen.getByTestId("version-badge"));
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("opens on keyboard focus, closes on Escape, and has no What's new link off the release channel", async () => {
    const user = userEvent.setup();
    render(<VersionBadgeView info={PREVIEW} />);
    await user.tab();
    expect(screen.getByTestId("version-badge")).toHaveFocus();
    expect(screen.getByRole("tooltip")).toHaveTextContent("Channelpreview");
    expect(screen.queryByRole("link", { name: "What's new" })).toBeNull();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("shows unknown for a missing build date", async () => {
    const user = userEvent.setup();
    render(<VersionBadgeView info={DEV} />);
    await user.hover(screen.getByTestId("version-badge"));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Builtunknown");
    expect(screen.queryByRole("link", { name: "What's new" })).toBeNull();
  });

  it("hides the commit under 480 px wide but keeps the version", () => {
    render(<VersionBadgeView info={RELEASE} />);
    expect(screen.getByTestId("version-badge-commit").className).toContain("max-[479px]:hidden");
    expect(screen.getByTestId("version-badge-version").className).not.toContain("hidden");
  });

  it("renders nothing when the build is entirely unknown", () => {
    const { container } = render(<VersionBadgeView info={{ version: null, commit: null, built_at: null, channel: null }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("VersionBadge", () => {
  it("loads GET /v1/version", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(RELEASE), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    render(<VersionBadge />);
    await waitFor(() => expect(screen.getByTestId("version-badge")).toHaveTextContent("v0.1.3 · 473b4fa"));
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/version");
  });

  it("stays hidden when the endpoint fails", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 500 }));
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<VersionBadge />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});
