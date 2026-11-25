# Seed 5678 run: extra information

Results: [`RESULTS.md`](RESULTS.md) and [`results.csv`](results.csv). Log: [`run.log`](run.log), an unchanged copy
of the log saved from the run (progress lines and the printed report).

## Code

Commit `a3c7653`.

## Author's checks

These are the author's own statement. Neither the log nor the result files record the commit or the git status.

> I (the author) checked before the run that `git status` was clean, and after the run that only the two new
> untracked folders existed.

The two folders are `benchmarks/data_seed5678/` and `benchmarks/results/runs/seed5678-router-fields/`.

## Timeouts

`grep -c -i timeout run.log` = **0**

This counts the lines of the log that contain the word "timeout", ignoring case.
