package update

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func stubSourceSeams(t *testing.T, helpers map[string]string, owned bool) {
	t.Helper()
	origLook := lookPath
	origOwns := pacmanOwns
	t.Cleanup(func() {
		lookPath = origLook
		pacmanOwns = origOwns
	})
	lookPath = func(name string) (string, error) {
		if path, ok := helpers[name]; ok {
			return path, nil
		}
		return "", errors.New("not found")
	}
	pacmanOwns = func(string) bool { return owned }
}

func TestDetectManager(t *testing.T) {
	cases := []struct {
		label       string
		source      string
		path        string
		helpers     map[string]string
		pacmanOwned bool
		wantName    string
		wantCommand string
		wantAdvice  bool
	}{
		{
			label:       "caskroom upgrades the tap cask",
			path:        "/opt/homebrew/Caskroom/agent-fleet/0.19.0/agent-fleet",
			wantName:    "Homebrew",
			wantCommand: "brew upgrade --cask pvluandinh/tap/agent-fleet",
		},
		{
			label:       "cellar upgrades the formula",
			path:        "/opt/homebrew/Cellar/agent-fleet/0.19.0/bin/agent-fleet",
			wantName:    "Homebrew",
			wantCommand: "brew upgrade agent-fleet",
		},
		{
			label:       "linuxbrew opt tree",
			path:        "/home/linuxbrew/.linuxbrew/opt/agent-fleet/bin/agent-fleet",
			wantName:    "Homebrew",
			wantCommand: "brew upgrade agent-fleet",
		},
		{
			label:       "homebrew ldflag without a brew path",
			source:      "Homebrew",
			path:        "/usr/local/bin/agent-fleet",
			wantName:    "Homebrew",
			wantCommand: "brew upgrade agent-fleet",
		},
		{
			label:       "mise install dir",
			path:        "/home/user/.local/share/mise/installs/ubi-pvluandinh-agent-fleet/0.19.0/agent-fleet",
			wantName:    "mise",
			wantCommand: "mise upgrade --bump ubi:PVLuanDinh/agent-fleet",
		},
		{
			label:       "mise custom data dir",
			path:        "/data/tools/installs/ubi-pvluandinh-agent-fleet/0.20.0/agent-fleet",
			wantName:    "mise",
			wantCommand: "mise upgrade --bump ubi:PVLuanDinh/agent-fleet",
		},
		{
			label:       "pacman owned with yay",
			path:        "/usr/bin/agent-fleet",
			helpers:     map[string]string{"yay": "/usr/bin/yay"},
			pacmanOwned: true,
			wantName:    "yay",
			wantCommand: "yay -S agent-fleet-bin",
		},
		{
			label:       "pacman owned with paru only",
			path:        "/usr/bin/agent-fleet",
			helpers:     map[string]string{"paru": "/usr/bin/paru"},
			pacmanOwned: true,
			wantName:    "paru",
			wantCommand: "paru -S agent-fleet-bin",
		},
		{
			label:       "pacman owned without a helper",
			path:        "/usr/bin/agent-fleet",
			pacmanOwned: true,
			wantName:    "pacman",
			wantAdvice:  true,
		},
		{
			label:      "nix store is updated through nix",
			path:       "/nix/store/0c1s7yb9hv2y1wkgvyd4jp3spdcyv0b0-agent-fleet-0.39.0/bin/agent-fleet",
			wantName:   "Nix",
			wantAdvice: true,
		},
		{
			label: "another ubi tool is direct",
			path:  "/home/user/.local/share/mise/installs/ubi-other-user-agent-fleet/1.0.0/agent-fleet",
		},
		{
			label: "install script dir is direct",
			path:  "/home/user/.local/bin/agent-fleet",
		},
		{
			label: "gopath is direct",
			path:  "/home/user/go/bin/agent-fleet",
		},
		{
			label: "manual opt prefix is direct",
			path:  "/opt/agent-fleet/bin/agent-fleet",
		},
	}
	for _, tc := range cases {
		t.Run(tc.label, func(t *testing.T) {
			stubSourceSeams(t, tc.helpers, tc.pacmanOwned)
			origSource := buildSource
			buildSource = tc.source
			t.Cleanup(func() { buildSource = origSource })

			manager := DetectManager(tc.path)
			if manager.Name != tc.wantName {
				t.Fatalf("Name = %q, want %q", manager.Name, tc.wantName)
			}
			if manager.String() != tc.wantCommand {
				t.Fatalf("Command = %q, want %q", manager.String(), tc.wantCommand)
			}
			if (manager.Advice != "") != tc.wantAdvice {
				t.Fatalf("Advice = %q, wantAdvice %v", manager.Advice, tc.wantAdvice)
			}
			if manager.Delegated() != (tc.wantCommand != "") {
				t.Fatalf("Delegated() = %v with command %q", manager.Delegated(), tc.wantCommand)
			}
		})
	}
}

func TestDetectManagerFollowsSymlink(t *testing.T) {
	stubSourceSeams(t, nil, false)
	dir := t.TempDir()
	caskroom := filepath.Join(dir, "Caskroom", "agent-fleet", "0.19.0")
	if err := os.MkdirAll(caskroom, 0o755); err != nil {
		t.Fatal(err)
	}
	real := filepath.Join(caskroom, "agent-fleet")
	if err := os.WriteFile(real, []byte("cask build"), 0o755); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "bin", "agent-fleet")
	if err := os.MkdirAll(filepath.Dir(link), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(real, link); err != nil {
		t.Fatal(err)
	}
	manager := DetectManager(link)
	if manager.String() != "brew upgrade --cask pvluandinh/tap/agent-fleet" {
		t.Fatalf("symlink into Caskroom got %q", manager.String())
	}
}

func TestPacmanOwnsOffLinux(t *testing.T) {
	if runtime.GOOS == "linux" {
		t.Skip("exercises the non-linux short circuit")
	}
	if pacmanOwnsPath("/usr/bin/agent-fleet") {
		t.Fatal("pacman ownership must be linux-only")
	}
}

func TestRestartTargetPrefersPathEntry(t *testing.T) {
	stubSourceSeams(t, map[string]string{"agent-fleet": "/opt/homebrew/bin/agent-fleet"}, false)
	got := RestartTarget("/opt/homebrew/Caskroom/agent-fleet/0.19.0/agent-fleet")
	if got != "/opt/homebrew/bin/agent-fleet" {
		t.Fatalf("RestartTarget = %q", got)
	}
}

func TestRestartTargetFallsBackToExecPath(t *testing.T) {
	stubSourceSeams(t, nil, false)
	got := RestartTarget("/weird/place/agent-fleet")
	if got != "/weird/place/agent-fleet" {
		t.Fatalf("RestartTarget = %q", got)
	}
}
