# hk-odds-logger

Loggt an Hongkong-Renntagen (HKJC, Sha Tin/Happy Valley) automatisch die
WIN- und PLACE-Quotenverläufe aller Rennen über GitHub Actions — kein eigener
Server nötig.

- `data/YYYY-MM-DD.jsonl` — Quoten-Snapshots (alle 1–3 Min; 60s-Takt in den
  letzten 15 Min vor jedem Start)
- `data/YYYY-MM-DD_<VENUE>_meta.json` — Startzeiten, Läufer, gearInfo
  (Equipment!), Ratings, Draw

Zeilenformat Snapshots:
`{"t": UTC-Zeit, "v": "ST|HV", "r": Rennen, "pool": "WIN|PLA", "h": Pferd-Nr, "o": Quote, "s": Verkaufsstatus}`

Zweck: Drift-Modell (erwartete Schlussquote), Cross-Pool-Analyse (WIN→PLA),
Late-Money-/CLV-Auswertung und Equipment-Historie für das Horse-Projekt.
Der PC-Agent zieht die Daten per `git pull` + `scripts/merge_odds_snapshots.py`
in `dataset/odds_snapshots.db`.

Manueller Testlauf: Actions → "HKJC Odds Logger" → Run workflow.
