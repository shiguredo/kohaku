package kohaku

type ErlangVmStats struct {
	Type string `json:"type" db:"stats_type"`
}

type ErlangVmMemoryStats struct {
	ErlangVmStats

	Total         uint64 `json:"total" db:"type_total"`
	Processes     uint64 `json:"processes" db:"type_processes"`
	ProcessesUsed uint64 `json:"processes_used" db:"type_processes_used"`
	System        uint64 `json:"system" db:"type_system"`
	Atom          uint64 `json:"atom" db:"type_atom"`
	AtomUsed      uint64 `json:"atom_used" db:"type_atom_used"`
	Binary        uint64 `json:"binary" db:"type_binary"`
	Code          uint64 `json:"code" db:"type_code"`
	ETS           uint64 `json:"ets" db:"type_ets"`
}
