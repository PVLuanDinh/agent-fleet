package mcpreg

// The package's tests exercise the per-tool MCP registration path; the
// agent-fleet CLI-only default is pinned in cli_only_test.go.
func init() { CLIOnly = false }
