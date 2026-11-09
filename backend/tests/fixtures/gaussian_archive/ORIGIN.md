# Origin of the Gaussian archive-block fixtures

Both files are real Gaussian 16 Rev C.02 output (wB97X-D/def2-TZVP, `Opt=Tight Freq Int=UltraFine SCF=Tight NoSymm`),
trimmed to the parts the Hessian reader uses. Lines of the original that were removed are marked ` ... (trimmed)`;
nothing inside a kept section was edited. Kept: the banner, the route, the last `Input orientation` table, every
`Frequencies --` line, (ethanethiol only) the force-constant block, and the last archive block (`Freq` step).

* `n_butane_g16_archive_only.log` -- n-butane, 14 atoms. The original prints no `Force constants in Cartesian
  coordinates` block; the archive carries the 903-value lower triangle (3N(3N+1)/2, N = 14).
* `ethanethiol_g16_both_blocks.log` -- ethanethiol, 9 atoms. The original prints both the force-constant block and
  the archive Hessian, so the two can be compared.

Source: the E2 basin-tolerance study, `E2/raw_outputs/tasks/wb97xd/{n_butane,ethanethiol}/c00/job.log`
(Zeus, 2026-10-26).

The user name, scratch path and host in the original were replaced by `username`/`USERNAME`, `xxxx-master` and
`NODE` (same lengths, so the 70-column archive wrap is unchanged).
