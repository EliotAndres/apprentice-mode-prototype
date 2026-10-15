### hard tier (8 sessions)

| model | input | P@5 | R@10 | R@10 95% CI | evidence IoU | inst F1 | FP neg | time rec* | param rec | solved | recon | feas | s/session | $/session |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| heuristic | trace | 0.53 | 0.66 | 0.50-0.80 | 0.53 | 0.61 | 0.16 | 0.66 | 0.14 | 0.00 | 2.43 | 1.67 | 0 | 0.00 |
| sonnet | trace | 0.85 | 0.77 | 0.61-0.92 | 0.86 | 0.89 | 0.11 | 0.88 | 0.60 | 0.38 | 4.17 | 3.42 | 9 | 0.04 |
| sonnet | both | 0.84 | 0.85 | 0.69-0.97 | 0.88 | 0.95 | 0.16 | 0.93 | 0.53 | 0.38 | 4.26 | 3.63 | 19 | 0.22 |

### agent tier (9 sessions)

| model | input | P@5 | R@10 | R@10 95% CI | evidence IoU | inst F1 | FP neg | time rec* | param rec | solved | recon | feas | s/session | $/session |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| heuristic | trace | 0.28 | 0.52 | 0.22-0.81 | 0.42 | 0.07 | 0.06 | 0.52 | 0.62 | 0.00 | 1.45 | 1.09 | 0 | 0.00 |
| sonnet | trace | 0.94 | 1.00 | 1.00-1.00 | 0.91 | 0.94 | 0.00 | 1.00 | 0.93 | 0.89 | 4.00 | 3.11 | 10 | 0.05 |
| sonnet | both | 0.88 | 1.00 | 1.00-1.00 | 0.89 | 0.90 | 0.03 | 1.00 | 0.93 | 0.78 | 4.21 | 3.16 | 17 | 0.30 |

### all tier (17 sessions)

| model | input | P@5 | R@10 | R@10 95% CI | evidence IoU | inst F1 | FP neg | time rec* | param rec | solved | recon | feas | s/session | $/session |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| heuristic | trace | 0.40 | 0.58 | 0.40-0.77 | 0.49 | 0.40 | 0.11 | 0.59 | 0.33 | 0.00 | 2.09 | 1.47 | 0 | 0.00 |
| sonnet | trace | 0.90 | 0.89 | 0.80-0.97 | 0.88 | 0.92 | 0.05 | 0.94 | 0.77 | 0.65 | 4.09 | 3.28 | 10 | 0.05 |
| sonnet | both | 0.86 | 0.93 | 0.84-1.00 | 0.88 | 0.92 | 0.09 | 0.97 | 0.74 | 0.59 | 4.24 | 3.43 | 17 | 0.26 |

### test tier (17 sessions)

| model | input | P@5 | R@10 | R@10 95% CI | evidence IoU | inst F1 | FP neg | time rec* | param rec | solved | recon | feas | s/session | $/session |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| heuristic | trace | 0.40 | 0.58 | 0.40-0.77 | 0.49 | 0.40 | 0.11 | 0.59 | 0.33 | 0.00 | 2.09 | 1.47 | 0 | 0.00 |
| sonnet | trace | 0.90 | 0.89 | 0.80-0.97 | 0.88 | 0.92 | 0.05 | 0.94 | 0.77 | 0.65 | 4.09 | 3.28 | 10 | 0.05 |
| sonnet | both | 0.86 | 0.93 | 0.84-1.00 | 0.88 | 0.92 | 0.09 | 0.97 | 0.74 | 0.59 | 4.24 | 3.43 | 17 | 0.26 |

### test_human tier (8 sessions)

| model | input | P@5 | R@10 | R@10 95% CI | evidence IoU | inst F1 | FP neg | time rec* | param rec | solved | recon | feas | s/session | $/session |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| heuristic | trace | 0.53 | 0.66 | 0.50-0.80 | 0.53 | 0.61 | 0.16 | 0.66 | 0.14 | 0.00 | 2.43 | 1.67 | 0 | 0.00 |
| sonnet | trace | 0.85 | 0.77 | 0.61-0.92 | 0.86 | 0.89 | 0.11 | 0.88 | 0.60 | 0.38 | 4.17 | 3.42 | 9 | 0.04 |
| sonnet | both | 0.84 | 0.85 | 0.69-0.97 | 0.88 | 0.95 | 0.16 | 0.93 | 0.53 | 0.38 | 4.26 | 3.63 | 19 | 0.22 |

### test_agent tier (9 sessions)

| model | input | P@5 | R@10 | R@10 95% CI | evidence IoU | inst F1 | FP neg | time rec* | param rec | solved | recon | feas | s/session | $/session |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| heuristic | trace | 0.28 | 0.52 | 0.22-0.81 | 0.42 | 0.07 | 0.06 | 0.52 | 0.62 | 0.00 | 1.45 | 1.09 | 0 | 0.00 |
| sonnet | trace | 0.94 | 1.00 | 1.00-1.00 | 0.91 | 0.94 | 0.00 | 1.00 | 0.93 | 0.89 | 4.00 | 3.11 | 10 | 0.05 |
| sonnet | both | 0.88 | 1.00 | 1.00-1.00 | 0.89 | 0.90 | 0.03 | 1.00 | 0.93 | 0.78 | 4.21 | 3.16 | 17 | 0.30 |

Macro-averaged over sessions; 'all' pools the hard (human multitask) and agent tiers. CI: session bootstrap. *time recall uses synthetic timing; indicative only. Cost is the CLI-reported API-equivalent cost per session, latency the median wall time per call.

Judge: sonnet (1-5 scales); second judge opus on the same 15 items agrees within 1 point on 100% (reconstruction) and 100% (feasibility), exactly on 80% / 80%.
