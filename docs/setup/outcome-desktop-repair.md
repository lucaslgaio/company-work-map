# Desktop outcome repair — a32

This is a scoped engineering receipt for q1/q2/q3/q5/q6, outcomes o1/o5/o6. It is not founder acceptance, a new simulation replay, or a claim of native/online readiness. Original Grok exports, p40/p41 reports and the failing before evidence remain unchanged.

## P1 / I022 / o5: choices take effect before persistence

Before: I022 (`Nubank - Fatura do cartão pessoal`) entered temporary local metadata because the personal list was empty. A later business marker `Fatura` could promote it into the local outbox. That evidence did not establish an original remote upload. Existing explicit exclusion precedence was already correct; setup did not make the choice effective.

After: each participant reviews precise initial phrases (`cartão pessoal`, `conta pessoal`, `e-mail pessoal`) and supplies their own additions. The desktop saves raw phrases only in `personal-policy.json`; `participation-choices.json` contains participation/image booleans, policy version and the private file digest. The brain records those choices for the authenticated participant. Capture remains blocked before reading a surface if the private file is missing, modified or unacknowledged, or the company policy/access state is pending/unsupported. Images require both company permission and the participant's choice.

The new local proof executes the actual desktop command, pending/acknowledged status, Collector and durable sender. With `Fatura` active, I022 is absent from SQLite quarantine/outbox and all sender payloads; text/image calls are zero for that surface. `Inter Empresas - Fatura de fornecedor` remains permitted and reaches a **local synthetic receiver**. No bank or Chrome application block was added. A personal window without a recognized private signal can still remain unidentified; these phrases do not establish universal privacy recognition.

## B3 / I012, B10 / o1/o6: recognize records and preserve time

Before: a descriptive paragraph about the business did not literally match `Cronograma Obra AVM 82 - Planilhas Google`. Later policy changes promoted old metadata, with no current body observation.

After: `Policy.from_brain` consumes active, source-backed literal `recognition_markers`, separate from descriptive context. The brain owns grounding/retirement; the desktop does not extract names from prose. Pending metadata is reclassified under the latest effective policy. An excluded temporary item is removed. A permitted item can produce an explicitly metadata-only old event; a separate current event reads body/image only if the **same** identity is still focused and permitted. A closed/changed/private window is never opened or read by this recovery. The current event has the current timestamp. No prior body is fabricated.

The probe retains both metadata and current evidence for the schedule and verifies the original unread body is absent. Tests also change focus before promotion and verify no body from the other window is read. Grounded-marker generation by a live model remains untested here.

## B4/B15 / o1/o6: unknown counts and expiry remain visible

Before: app/hour deduplication hid multiple unknown windows. Temporary metadata expired without visible loss accounting.

After: random occurrence identities yield opaque context keys; safe updates retain per-device/app/period samples and distinct window counts without titles, references, private terms, body or image. The authenticated brain supplies the actor identity. A surviving unknown occurrence retains its first observation-hour as period start and extends period end; a clock-hour change alone does not create a new unresolved context. Context identity distinguishes unknown documents while distinct-window counts use native window/process identity. Cumulative updates are bounded to five-minute intervals, plus newly observed contexts and explicit fixture endpoints; they are new immutable events, never overwritten event IDs. Brain/Nora own grouping and monotonic retry handling.

`status.coverage` exposes counts, expiry and pending expiry updates, including an explicit statement that these counts prove neither complete coverage nor inactivity. Excluded personal windows do not enter the unknown-work counts. Temporary metadata still expires after 24 hours. Expiry produces count-only updates on the same opaque context key when capture policy permits; offline/capacity failures remain visible and retryable. The probe verifies two distinct unknown windows and two safe expiry updates. Anonymous period history is bounded; pending expiry periods are retained until queued. Counts report this collector's unresolved context, not proved missed company tasks.

Development `run --development --fixture FILE --once --clock ISO` aligns a local fixture with its simulated time. This override requires the local fixture receiver and is rejected before constructing a native adapter on other paths. Production defaults use the system clock.

## I1/I5/I6/I7 / o6: failures identify the next step

Linux was outside the supported macOS 14+/Windows 11+ scope. Its refusal is not evidence of a supported-platform capture failure. OS/version and Python 3.12+ now have separate checks. Source installation checks build/system tools before writes. Registration scripts check platform/version and required registration tools before startup actions.

`doctor` constructs NativeAdapter inside the diagnostic boundary, so unsupported construction produces structured checks and `ready:false`. Readiness still requires a current effective policy, authenticated writable transport, local tools and fresh health from the actual registered process. An interactive probe cannot substitute for that health. A failed `enable-capture` persists capture disabled.

GitHub setup distinguishes a missing data branch, an existing uninitialized branch and a synthetic/company mode mismatch, using narrow safe codes instead of raw provider exceptions. Mocks verify all three through the actual `configure` command and verify no config is created after refusal. These checks do not retrospectively establish which provider failure caused the original run. Successful configure emits `configured_values.repository/state_branch/mode/device_id/config_path`, for exact team generation by the root setup owner.

## Reproduction and boundary

```sh
python3 -m unittest discover -s tests/desktop -q
PYTHONPATH=. python3 tests/desktop/outcome_probe.py > /tmp/outcome-desktop-receipt.json
sh -n install/register-macos.sh
```

The candidate passed 61 desktop checks (real: command output from this worktree) and the asserted actual-output probe. Existing denial/cache, image revocation, focus, retry/idempotency, occurrence IDs and atomic-capacity checks remain. The old denial checks now distinguish cache currency from capture authorization, so missing new consent cannot make revocation tests pass trivially. The old two-gap/hour expectation changed to 24 bounded cumulative updates with 240 counted samples; no raw unknown title was added to outbound data.

`outcome-desktop-receipt.json` pins source hashes and retains actual command output and safe payloads. Paths/identities are temporary synthetic values. No native capture/probe, permissions, startup registration, account/access mutation, GitHub upload, model call or channel send occurred. Actual Windows execution and recurring registered capture remain unverified. Root owns integrated brain/Nora/CLI proof, final founder copy and independent review; the candidate does not close their outcomes.
