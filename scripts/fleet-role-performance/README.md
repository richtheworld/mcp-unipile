# Fleet AI role performance

This is a read-only Unipile V2 report for the nine listed Fleet AI LinkedIn
Recruiter roles.

The report keeps a local role inventory for seven days. During that window it
refreshes the nine known job postings directly instead of scanning the full
Recruiter project and job catalogues. A full V2 inventory refresh happens when
the inventory is missing, stale, or invalid.

## Run

The wrapper reads `UNIPILE_V2_API_KEY` from the environment or the macOS
Keychain service `com.dynamism.unipile.v2.api-key` without printing it. If no
`UNIPILE_V2_LINKEDIN_ACCOUNT_ID` is provided, the report discovers the single
running LinkedIn Recruiter account through V2.

```sh
UNIPILE_METRICS_RESULT_PATH="$PWD/work/latest_fleet_metrics_v2.json" \
  ./scripts/fleet-role-performance/run_unipile_role_performance_v2.zsh
```

The history, inventory, and result files are local runtime state and should not
be committed. The report never uses legacy API credentials, legacy endpoints,
or a non-V2 fallback.
