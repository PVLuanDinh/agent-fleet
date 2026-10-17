package ui

import "testing"

// withOwnBuild runs a test as the agent-fleet build ships.
func withOwnBuild(t *testing.T) {
	t.Helper()
	ownBuild = true
	t.Cleanup(func() { ownBuild = false })
}

// The agent-fleet build never asks upstream for releases or announcements,
// so it can never offer to self-update into stock agent-manager.
func TestOwnBuildMakesNoUpstreamCalls(t *testing.T) {
	withOwnBuild(t)
	m := buildModel(t)
	m.update.version = "0.41.0"
	for _, force := range []bool{false, true} {
		up, ok := m.fetchUpdates(force).(updateMsg)
		if !ok || up.latest != "" || up.url != "" || len(up.releases) != 0 || up.failed {
			t.Fatalf("force=%v: release check reached out: %+v", force, up)
		}
		fd, ok := m.fetchFeed(force).(feedMsg)
		if !ok || len(fd.messages) != 0 || fd.failed {
			t.Fatalf("force=%v: feed reached out: %+v", force, fd)
		}
	}
}

// Nothing covers the fleet at launch: the greeting waits under the messages
// key, and the version it greeted is still recorded.
func TestOwnBuildStartupLeavesThePanelClosed(t *testing.T) {
	withOwnBuild(t)
	m := buildModel(t)
	m.update.version = "0.41.0"
	m.mode = modeList
	m.openStartupNotice()
	if m.mode == modeNotices {
		t.Fatal("the agent-fleet build opened the messages panel by itself at launch")
	}
	if len(m.activeNotices()) == 0 {
		t.Fatal("the greeting should still wait in the messages panel")
	}
	m.openNotices("")
	if m.mode != modeNotices {
		t.Fatal("the messages panel must still open on request")
	}
}
