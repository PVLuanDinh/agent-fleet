package ui

// ownBuild marks the agent-fleet build of this TUI. It is a fork with its own
// releases, so it never polls upstream's release list or announcement feed,
// never offers to self-update into upstream's binary, and never opens the
// messages panel on its own: notices wait under the messages key. Tests that
// exercise upstream's feed and update flow flip it off for their duration.
var ownBuild = true
