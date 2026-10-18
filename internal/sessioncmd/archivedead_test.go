package sessioncmd

import (
	"testing"

	"github.com/PVLuanDinh/agent-fleet/internal/status"
)

// archive-dead files away exactly the rows that are dead with no pane left:
// a live session and a dead-marked session whose pane still runs stay put.
func TestArchiveDeadFilesOnlyDeadSessionsWithNoPane(t *testing.T) {
	h := newSessionHarness(t)
	gone, err := h.sessions.Create(h.caller.ID, CreateSessionOptions{Name: "gone"})
	if err != nil {
		t.Fatalf("Create gone: %v", err)
	}
	live, err := h.sessions.Create(h.caller.ID, CreateSessionOptions{Name: "live"})
	if err != nil {
		t.Fatalf("Create live: %v", err)
	}
	stale, err := h.sessions.Create(h.caller.ID, CreateSessionOptions{Name: "stale-mark"})
	if err != nil {
		t.Fatalf("Create stale: %v", err)
	}
	if _, err := h.sessions.Kill(h.caller.ID, gone.ID); err != nil {
		t.Fatalf("Kill: %v", err)
	}
	for _, id := range []string{gone.ID, stale.ID} {
		if err := h.store.UpdateStatus(id, status.Dead); err != nil {
			t.Fatalf("UpdateStatus %s: %v", id, err)
		}
	}
	archived, err := h.sessions.ArchiveDead()
	if err != nil {
		t.Fatalf("ArchiveDead: %v", err)
	}
	if len(archived) != 1 || archived[0].ID != gone.ID {
		t.Fatalf("archived = %+v, want only %s", archived, gone.ID)
	}
	for id, want := range map[string]bool{gone.ID: true, live.ID: false, stale.ID: false} {
		row, err := h.store.Get(id)
		if err != nil || row.Archived != want {
			t.Fatalf("%s archived = %v (err %v), want %v", id, row.Archived, err, want)
		}
	}
	again, err := h.sessions.ArchiveDead()
	if err != nil || len(again) != 0 {
		t.Fatalf("second run = %+v, %v; want nothing left to archive", again, err)
	}
}
