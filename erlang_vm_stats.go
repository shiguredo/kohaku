package kohaku

type ErlangVmStats struct {
	Type string `json:"type" validate:"required" db:"stats_type"`
}

type ErlangVmMemoryStats struct {
	ErlangVmStats

	Memory        uint64 `json:"memory" db:"memory"`
	Processes     uint64 `json:"processes" db:"processes"`
	ProcessesUsed uint64 `json:"processes_used" db:"processes_used"`
	System        uint64 `json:"system" db:"system"`
	Atom          uint64 `json:"atom" db:"atom"`
	AtomUsed      uint64 `json:"atom_used" db:"atom_used"`
	Binary        uint64 `json:"binary" db:"binary"`
	Code          uint64 `json:"code" db:"code"`
	ETS           uint64 `json:"ets" db:"ets"`
}
