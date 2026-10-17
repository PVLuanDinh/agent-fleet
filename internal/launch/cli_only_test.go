package launch

import (
	"testing"

	"github.com/PVLuanDinh/agent-fleet/internal/config"
	"github.com/PVLuanDinh/agent-fleet/internal/mcpreg"
)

// The agent-fleet build registers no MCP server into any session, and every
// session is told where the same abilities live: the CLI.
func TestCLIOnlyDefaultPointsEverySessionAtTheCLI(t *testing.T) {
	mcpreg.CLIOnly = true
	t.Cleanup(func() { mcpreg.CLIOnly = false })
	for _, name := range []string{"claude", "codex", "gemini", "opencode"} {
		if style := mcpreg.Style(name, ""); style != mcpreg.StyleNone {
			t.Fatalf("%s registers MCP style %q, want none", name, style)
		}
		if note := coordinationNote(name, config.Tool{}, true); note != ProactiveCoordinationNote {
			t.Fatalf("%s gets note %q, want the CLI coordination note", name, note)
		}
	}
}
