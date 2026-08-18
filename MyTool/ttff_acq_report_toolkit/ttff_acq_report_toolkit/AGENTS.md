# For AI agents

Read `README.md` in this folder first, then run `ttff_chobs_acq_report.py`.

- Must use `bin/bpdebug_track_dump.exe` (ProtocolDecoder.dll). Do not hand-parse TrackInfoExt.
- 星历有效 = sat_state bit29; 参与解算 = sat_state bit27; 可参与位置解 = pvt_state bit31.
- X-axis = seconds from Reset @ 10 Hz (no UTC).
