package sessioncmd

import "testing"

func TestCLIVocabulary(t *testing.T) {
	want := Vocabulary{
		ListSessions:   "agent-fleet sessions",
		ListTerminals:  "agent-fleet terminal list",
		ListTasks:      "agent-fleet task list",
		ListGroups:     "agent-fleet groups",
		CreateGroup:    "agent-fleet create-group",
		CreateTerminal: "agent-fleet terminal create",
		SpawnTool:      "agent-fleet spawn --tool",
		SpawnModel:     "agent-fleet spawn --model",
		SpawnEffort:    "agent-fleet spawn --effort",
		SpawnProfile:   "agent-fleet spawn --profile",
		Revive:         "agent-fleet revive",
		Restore:        "agent-fleet archive --restore",
		Send:           "agent-fleet send",
		Read:           "agent-fleet read",
		ArchiveSelf:    "agent-fleet archive-self",
		KillSelf:       "agent-fleet kill-self",
	}
	if got := CLIVocabulary(); got != want {
		t.Fatalf("CLIVocabulary() = %+v, want %+v", got, want)
	}
}
