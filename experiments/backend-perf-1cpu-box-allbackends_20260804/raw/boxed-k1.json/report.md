# Counter2 shootout

Run `20260804T070525Z` on `devbig014.atn7.facebook.com` at Reverie `bfea4d5aa7d662cacf21f41ff2df5b60925dff2d`.
Each workload has 3 measured repetitions after 1 warmup(s).
Slowdown is median backend wall time / median matching native-variant wall time.

| Backend | Workloads | Geomean slowdown |
| --- | ---: | ---: |
| liteinst | 2 | 1.027x |
| e9patch | 2 | 1.044x |
| sabre | 2 | 1.049x |
| ptrace | 2 | 1.569x |
| dbi | 2 | 5.549x |

| Workload | Backend | Variant | Median ms | Native ms | Slowdown | Counter2 calls |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| counter2-cpu-heavy | dbi | dynamic | 16305.5 | 3004.1 | 5.428x | 16956 |
| counter2-cpu-heavy | e9patch | dynamic | 3055.3 | 3004.1 | 1.017x | 16925 |
| counter2-cpu-heavy | liteinst | dynamic | 3013.3 | 3004.1 | 1.003x | 16925 |
| counter2-cpu-heavy | ptrace | dynamic | 3262.9 | 3004.1 | 1.086x | 16957 |
| counter2-cpu-heavy | sabre | dynamic | 3074.6 | 3004.1 | 1.023x | 16926 |
| counter2-syscall-mix | dbi | dynamic | 17035.7 | 3002.6 | 5.674x | 269583 |
| counter2-syscall-mix | e9patch | dynamic | 3216.2 | 3002.6 | 1.071x | 269552 |
| counter2-syscall-mix | liteinst | dynamic | 3158.9 | 3002.6 | 1.052x | 269552 |
| counter2-syscall-mix | ptrace | dynamic | 6802.6 | 3002.6 | 2.266x | 269584 |
| counter2-syscall-mix | sabre | dynamic | 3230.5 | 3002.6 | 1.076x | 269553 |
