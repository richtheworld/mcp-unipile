"""Guarded command-line interface for Unipile LinkedIn Recruiter."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from .recruiter_client import (
    DEFAULT_BASE_URL,
    READ_METHODS,
    RecruiterClient,
    UnipileAPIError,
    V1RecruiterClient,
    profile_identifier_schema,
)


CAPABILITIES = {
    "read_only": [
        "accounts",
        "applicants (v1 job IDs or v2 project IDs)",
        "profiles and Recruiter Open to Work",
        "LinkedIn identity conversion to canonical v2 profile IDs",
        "projects list/get",
        "Recruiter people search",
        "Recruiter search parameters",
        "pipeline candidates",
        "InMail credits",
        "arbitrary API GET requests",
    ],
    "mutations_requiring_execute_and_confirmation": [
        "save candidate to project/pipeline",
        "create project",
        "edit project",
        "raw LinkedIn proxy writes",
        "arbitrary non-read API requests",
    ],
    "api": {
        "default": "v2",
        "select_with": "--backend v1|v2",
        "fallback": "disabled",
        "v1": "explicit read-only historical audit",
        "v2": "production reads and the only mutation backend",
    },
    "control_plane": [
        "each backend loads only its version-specific credentials and account ID",
        "mutations require an exact dry-run confirmation token",
        "v1 and v2 IDs are never translated or reused implicitly",
    ],
}


V1_COMMANDS = {"accounts", "doctor", "projects", "project", "applicants", "request"}


def load_json(value: Optional[str]) -> dict[str, Any]:
    if not value:
        return {}
    if value == "-":
        data = json.load(sys.stdin)
    elif value.lstrip().startswith(("{", "[")):
        data = json.loads(value)
    else:
        data = json.loads(Path(value).read_text())
    if not isinstance(data, dict):
        raise ValueError("JSON input must be an object")
    return data


def print_json(value: Any, pretty: bool = True) -> None:
    print(json.dumps(value, indent=2 if pretty else None, sort_keys=pretty, default=str))


def require_mutation(args: argparse.Namespace, token: str) -> None:
    if not getattr(args, "execute", False):
        raise ValueError(
            f"Dry run only. Re-run with --execute --confirm '{token}' to mutate LinkedIn."
        )
    if getattr(args, "confirm", None) != token:
        raise ValueError(f"Confirmation token must exactly equal: {token}")


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=("v1", "v2"),
        default=os.getenv("UNIPILE_RECRUITER_BACKEND", "v2"),
        help="Explicit API backend; defaults to v2 and never falls back",
    )
    parser.add_argument(
        "--base-url",
        help="Override the selected backend's environment-configured base URL",
    )
    parser.add_argument(
        "--account-id",
        help="Version-specific LinkedIn account ID; auto-discovers when unambiguous",
    )
    parser.add_argument(
        "--min-request-interval-seconds",
        type=float,
        default=float(os.getenv("UNIPILE_V2_MIN_REQUEST_INTERVAL_SECONDS", "1.1")),
        help=(
            "Minimum delay after each v2 provider request; defaults to 1.1 seconds "
            "and can be raised for batch safety policies"
        ),
    )
    parser.add_argument("--compact", action="store_true", help="Emit compact JSON")


def add_mutation_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unipile-recruiter",
        description="Guarded LinkedIn Recruiter CLI with explicit Unipile v1/v2 selection",
    )
    add_connection_args(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capabilities", help="Show supported CLI capabilities")
    sub.add_parser("doctor", help="Validate credentials, account, and project access")
    sub.add_parser("accounts", help="List connected accounts")

    projects = sub.add_parser("projects", help="List Recruiter projects")
    projects.add_argument("--limit", type=int, default=100)
    projects.add_argument("--cursor")
    projects.add_argument("--offset", type=int)
    projects.add_argument("--keywords")

    project = sub.add_parser("project", help="Get one Recruiter project")
    project.add_argument("project_id")

    applicants = sub.add_parser(
        "applicants",
        help="List applicants using a v1 job ID or v2 project ID",
    )
    applicants.add_argument(
        "resource_id", help="V1 job ID when --backend v1; V2 project ID when --backend v2"
    )
    applicants.add_argument("--limit", type=int)
    applicants.add_argument("--cursor")
    applicants.add_argument(
        "--body", help="Optional v2 filter JSON object/file/-; unsupported by v1"
    )

    create = sub.add_parser("project-create", help="Create a Recruiter project")
    create.add_argument("--body", required=True, help="JSON object, file path, or -")
    add_mutation_args(create)

    edit = sub.add_parser("project-edit", help="Edit a Recruiter project")
    edit.add_argument("project_id")
    edit.add_argument("--body", required=True, help="JSON object, file path, or -")
    add_mutation_args(edit)

    profile = sub.add_parser("profile", help="Retrieve a LinkedIn profile variant")
    profile.add_argument(
        "identifier",
        help="Provider user ID, LinkedIn /in/ profile, or Recruiter profile URL",
    )
    profile.add_argument(
        "--variant", choices=("classic", "recruiter", "sales_navigator"), default="recruiter"
    )

    otw = sub.add_parser("open-to-work", help="Check Recruiter-visible Open to Work")
    otw.add_argument(
        "identifier",
        help="Candidate ID, LinkedIn /in/ profile, or Recruiter profile URL",
    )

    convert = sub.add_parser(
        "convert-identifier",
        help="Convert a LinkedIn profile reference to the canonical v2 identity schema",
    )
    convert.add_argument(
        "identifier",
        help="Provider ID, LinkedIn /in/ profile/slug, or Recruiter profile URL",
    )
    convert.add_argument(
        "--plan-only",
        action="store_true",
        help="Emit the deterministic v2 request schema without credentials or network calls",
    )

    search = sub.add_parser("search", help="Perform structured Recruiter people search")
    search.add_argument("--body", required=True, help="JSON object, file path, or -")
    search.add_argument("--limit", type=int, default=25)
    search.add_argument("--cursor")

    search_url = sub.add_parser("search-url", help="Run a Recruiter search URL")
    search_url.add_argument("url")
    search_url.add_argument("--limit", type=int, default=25)
    search_url.add_argument("--cursor")

    params = sub.add_parser("search-parameters", help="Resolve LinkedIn search parameter IDs")
    params.add_argument("type")
    params.add_argument("--keywords")
    params.add_argument(
        "--source",
        choices=("SEARCH", "JOB_POSTING", "APPLICANTS", "PIPELINE", "JOBS"),
        default="SEARCH",
    )
    params.add_argument("--project-id")
    params.add_argument("--stage-id")
    params.add_argument("--offset", type=int)
    params.add_argument("--limit", type=int, default=100)

    pipeline = sub.add_parser("pipeline", help="List/filter project pipeline candidates")
    pipeline.add_argument("project_id")
    pipeline.add_argument("--body", help="Optional filter JSON object/file/-")
    pipeline.add_argument("--limit", type=int, default=25)
    pipeline.add_argument("--cursor")

    save = sub.add_parser("save", help="Preview or save candidate into Recruiter project")
    save.add_argument("candidate_id", help="Recruiter candidate/profile ID")
    save.add_argument("--project", required=True, dest="project_id")
    save.add_argument(
        "--stage",
        required=True,
        help="Exact Recruiter pipeline stage ID",
    )
    save.add_argument(
        "--skip-project-check",
        action="store_true",
        help="Skip read-only project validation (not recommended)",
    )
    save.add_argument(
        "--skip-duplicate-check",
        action="store_true",
        help="Skip pipeline duplicate detection",
    )
    add_mutation_args(save)

    sub.add_parser("inmail-credits", help="Read InMail credit balances")

    proxy = sub.add_parser("proxy", help="Call a raw LinkedIn endpoint through Unipile")
    proxy.add_argument("--body", required=True, help="Proxy request JSON object/file/-")
    add_mutation_args(proxy)

    direct = sub.add_parser("request", help="Call an arbitrary Unipile API path")
    direct.add_argument("method", choices=("GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"))
    direct.add_argument("path")
    direct.add_argument("--params", help="Query JSON object/file/-")
    direct.add_argument("--body", help="Body JSON object/file/-")
    add_mutation_args(direct)
    return parser


def get_client(args: argparse.Namespace) -> RecruiterClient | V1RecruiterClient:
    if args.backend == "v1":
        api_key = os.getenv("UNIPILE_V1_API_KEY") or os.getenv("UNIPILE_API_KEY")
        base_url = (
            args.base_url
            or os.getenv("UNIPILE_V1_BASE_URL")
            or os.getenv("UNIPILE_V1_DSN")
            or os.getenv("UNIPILE_BASE_URL")
            or os.getenv("UNIPILE_DSN")
        )
        if not api_key or not base_url:
            raise ValueError(
                "Set UNIPILE_V1_API_KEY and UNIPILE_V1_BASE_URL for explicit v1 reads"
            )
        return V1RecruiterClient(api_key=api_key, base_url=base_url)
    api_key = os.getenv("UNIPILE_V2_API_KEY")
    if not api_key:
        raise ValueError("Set UNIPILE_V2_API_KEY for v2 operations")
    return RecruiterClient(
        api_key=api_key,
        base_url=args.base_url or os.getenv("UNIPILE_V2_BASE_URL", DEFAULT_BASE_URL),
        min_request_interval_seconds=args.min_request_interval_seconds,
    )


def account_id(
    client: RecruiterClient | V1RecruiterClient, args: argparse.Namespace
) -> str:
    if args.account_id:
        return args.account_id
    configured = (
        os.getenv("UNIPILE_V1_LINKEDIN_ACCOUNT_ID")
        if args.backend == "v1"
        else os.getenv("UNIPILE_V2_LINKEDIN_ACCOUNT_ID")
    )
    if configured:
        return configured
    return client.discover_linkedin_account()


def dry_run(operation: str, token: str, request: Mapping[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "dry_run": True,
        "operation": operation,
        "request": request,
        "execute_with": {"execute": True, "confirm": token},
        **extra,
    }


def execute(args: argparse.Namespace) -> Any:
    if args.command == "capabilities":
        return CAPABILITIES
    if args.command == "convert-identifier" and args.plan_only:
        return profile_identifier_schema(args.identifier)
    if args.backend == "v1" and args.command not in V1_COMMANDS:
        raise ValueError(
            f"{args.command} is unavailable on the read-only v1 audit backend; use --backend v2"
        )
    if args.backend == "v1" and args.command == "request" and args.method not in READ_METHODS:
        raise ValueError("Unipile v1 is read-only; mutation requests are disabled")
    client = get_client(args)
    if args.command == "accounts":
        return {"api_version": client.api_version, "items": client.get_accounts()}
    aid = account_id(client, args)

    if args.command == "doctor":
        accounts = client.get_accounts()
        account = next((item for item in accounts if item.get("id") == aid), {})
        if args.backend == "v1":
            projects_error = None
            projects = {}
            try:
                projects = client.list_projects(aid, limit=1)
            except UnipileAPIError as error:
                projects_error = error.as_dict()
            return {
                "ok": bool(account) and projects_error is None,
                "api_version": "v1",
                "historical_audit_only": True,
                "automatic_fallback": False,
                "account_resolved": bool(aid),
                "recruiter_projects_accessible": projects_error is None,
                "project_count": projects.get("total_count")
                or projects.get("paging", {}).get("total"),
                "projects_error": projects_error,
            }
        assert isinstance(client, RecruiterClient)
        contracts = client.get_linkedin_contracts(aid)
        recruiter_contracts = [
            item
            for item in contracts.get("contracts", [])
            if str(item.get("product", "")).lower() == "recruiter"
        ]
        projects_error = None
        projects = {}
        try:
            projects = client.list_projects(aid, limit=1)
        except UnipileAPIError as error:
            projects_error = error.as_dict()
        product_status = (
            account.get("metadata", {}).get("products_connection_status", {})
            if isinstance(account.get("metadata"), dict)
            else {}
        )
        selected_contract = next(
            (item for item in recruiter_contracts if item.get("selected") is True), None
        )
        recruiter_running = str(product_status.get("recruiter", "")).lower() == "running"
        projects_accessible = projects_error is None
        return {
            "ok": bool(account) and recruiter_running and bool(selected_contract) and projects_accessible,
            "api_version": client.api_version,
            "account_resolved": bool(aid),
            "account_status": account.get("status"),
            "products_connection_status": product_status,
            "recruiter_contract_selected": bool(selected_contract),
            "recruiter_projects_accessible": projects_accessible,
            "project_count": projects.get("total_count") or projects.get("paging", {}).get("total"),
            "projects_error": projects_error,
        }
    if args.command == "projects":
        return client.list_projects(
            aid,
            limit=args.limit,
            cursor=args.cursor,
            offset=args.offset,
            keywords=args.keywords,
        )
    if args.command == "project":
        return client.get_project(aid, args.project_id)
    if args.command == "applicants":
        if args.backend == "v1":
            assert isinstance(client, V1RecruiterClient)
            if args.body:
                raise ValueError("--body is unsupported for v1 applicant reads")
            return client.list_job_applicants(
                aid,
                args.resource_id,
                cursor=args.cursor,
                limit=args.limit or 250,
            )
        assert isinstance(client, RecruiterClient)
        return client.list_project_applicants(
            aid,
            args.resource_id,
            load_json(args.body),
            cursor=args.cursor,
            limit=args.limit or 100,
        )
    if args.command == "request":
        params = load_json(args.params)
        body = load_json(args.body)
        token = f"REQUEST_{args.method}"
        if args.backend == "v1":
            assert isinstance(client, V1RecruiterClient)
            return client.direct_request(
                args.method, args.path, params=params, body=body
            )
        assert isinstance(client, RecruiterClient)
        if args.method not in READ_METHODS and not args.execute:
            return dry_run(
                "direct-request",
                token,
                {
                    "method": args.method,
                    "path": args.path,
                    "params": params,
                    "body": body,
                },
            )
        if args.method not in READ_METHODS:
            require_mutation(args, token)
        return client.direct_request(
            args.method, args.path, params=params, body=body
        )
    assert isinstance(client, RecruiterClient)
    if args.command == "project-create":
        body = load_json(args.body)
        token = "CREATE_PROJECT"
        if not args.execute:
            return dry_run("project-create", token, {"body": body})
        require_mutation(args, token)
        return client.create_project(aid, body)
    if args.command == "project-edit":
        body = load_json(args.body)
        token = f"EDIT_PROJECT:{args.project_id}"
        if not args.execute:
            return dry_run("project-edit", token, {"project_id": args.project_id, "body": body})
        require_mutation(args, token)
        return client.edit_project(aid, args.project_id, body)
    if args.command == "profile":
        return client.get_profile(aid, args.identifier, args.variant)
    if args.command == "open-to-work":
        return client.open_to_work(aid, args.identifier)
    if args.command == "convert-identifier":
        return client.convert_profile_identifier(aid, args.identifier)
    if args.command == "search":
        return client.search_people(
            aid, load_json(args.body), cursor=args.cursor, limit=args.limit
        )
    if args.command == "search-url":
        return client.search_from_url(aid, args.url, cursor=args.cursor, limit=args.limit)
    if args.command == "search-parameters":
        return client.list_search_parameters(
            aid,
            args.type,
            args.keywords,
            source=args.source,
            project_id=args.project_id,
            stage_id=args.stage_id,
            offset=args.offset,
            limit=args.limit,
        )
    if args.command == "pipeline":
        return client.list_pipeline(
            aid,
            args.project_id,
            load_json(args.body),
            cursor=args.cursor,
            limit=args.limit,
        )
    if args.command == "save":
        project = None
        if not args.skip_project_check:
            project = client.get_project(aid, args.project_id)
        token = f"SAVE:{args.project_id}:{args.candidate_id}"
        request = {
            "candidate_id": args.candidate_id,
            "project_id": args.project_id,
            "stage": args.stage,
        }
        existing = None
        if not args.skip_duplicate_check:
            existing = client.find_candidate_in_pipeline(
                aid, args.project_id, args.candidate_id
            )
            if existing:
                return {
                    "dry_run": not args.execute,
                    "operation": "save-candidate",
                    "skipped": True,
                    "reason": "candidate_already_in_project",
                    "request": request,
                }
        if not args.execute:
            return dry_run(
                "save-candidate",
                token,
                request,
                validated_project={"id": project.get("id"), "name": project.get("name")}
                if project
                else None,
                duplicate_check="not_found",
            )
        require_mutation(args, token)
        result = client.save_candidate(
            aid, args.project_id, args.candidate_id, args.stage
        )
        verified = None
        if not args.skip_duplicate_check:
            verified = client.find_candidate_in_pipeline(
                aid, args.project_id, args.candidate_id
            ) is not None
        return {
            "success": True,
            "operation": "save-candidate",
            "result": result,
            "verified_in_pipeline": verified,
        }
    if args.command == "inmail-credits":
        return client.get_inmail_credits(aid)
    if args.command == "proxy":
        body = load_json(args.body)
        embedded_method = str(body.get("method", "GET")).upper()
        token = f"PROXY_{embedded_method}"
        if embedded_method not in READ_METHODS and not args.execute:
            return dry_run("proxy", token, body)
        if embedded_method not in READ_METHODS:
            require_mutation(args, token)
        return client.proxy_request(aid, body)
    raise ValueError(f"Unknown command: {args.command}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = execute(args)
        print_json(result, pretty=not args.compact)
        return 0
    except UnipileAPIError as error:
        print_json({"error": error.as_dict()}, pretty=not args.compact)
        return 2
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print_json({"error": {"type": "cli_error", "detail": str(error)}}, pretty=not args.compact)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
