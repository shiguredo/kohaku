package kohaku

import (
	"context"
	"encoding/json"

	"github.com/doug-martin/goqu/v9"
	"github.com/jackc/pgx/v4/pgxpool"
)

// TODO(v): sqlc 化
func CollectorSoraNodeErlangVmStats(pool *pgxpool.Pool, stats SoraNodeErlangVmStats) error {
	if err := InsertSoraNode(context.Background(), pool, stats); err != nil {
		return err
	}

	erlangVm := &ErlangVm{
		Time:     stats.Timestamp,
		Label:    stats.Label,
		Version:  stats.Version,
		NodeName: stats.NodeName,
	}

	for _, v := range stats.Stats {
		stats := new(ErlangVmStats)
		if err := json.Unmarshal(v, &stats); err != nil {
			return err
		}

		// その後 type をみて struct をさらに別途デコードする
		// codec とかは定数かした方がいいのかもしれない
		switch stats.Type {
		case "memory":
			s := new(ErlangVmMemoryStats)
			if err := json.Unmarshal(v, &s); err != nil {
				return err
			}

			ds := goqu.Insert("erlang_vm_memory_stats").Rows(
				ErlangVmMemory{
					ErlangVm:            *erlangVm,
					ErlangVmMemoryStats: *s,
				},
			)
			insertSQL, _, _ := ds.ToSQL()
			_, err := pool.Exec(context.Background(), insertSQL)
			if err != nil {
				return err
			}
		default:
		}
	}
	return nil
}

// TODO(v): sqlc 化
func InsertSoraNode(ctx context.Context, pool *pgxpool.Pool, stats SoraNodeErlangVmStats) error {
	sq := goqu.Select("channel_id").
		From("sora_node").
		Where(goqu.Ex{
			"label":     stats.Label,
			"node_name": stats.NodeName,
			"version":   stats.Version,
		})
	le := goqu.L("NOT EXISTS ?", sq)

	ds := goqu.Insert("sora_node").
		Cols(
			"timestamp",

			"label",
			"version",
			"node_name",
		).
		FromQuery(
			goqu.Select(
				goqu.L("?, ?, ?, ?",
					stats.Timestamp,

					stats.Label,
					stats.Version,
					stats.NodeName,
				),
			).Where(le))
	insertSQL, _, _ := ds.ToSQL()
	if _, err := pool.Exec(ctx, insertSQL); err != nil {
		return err
	}

	return nil
}