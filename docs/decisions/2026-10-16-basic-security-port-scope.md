# Current port scope: basic local security

On October 16, Oren answered **"Approve as recommended"** to **"Approve this port
list for Seldon (from the legacy audit)?"** The contemporaneous CC handoff records
that answer and the list below. This decision changes current sequencing and
security requirements; it does not replace the full North Star or turn source
tests into product acceptance.

Approved local implementation:

- Minimal health and supported-provider preflight.
- Supported cancellation with truthful terminal state and recoverable resume.
- Basic stall/crash recovery: timeouts, no-progress detection and existing resume.
- Current candidate/claim binding and exact controller-observed process provenance.

Backlog, to revisit on a concrete failure:

- Validated claim leases and tokens.
- Attributable server-bound mail and revocation.
- Authenticated localhost for the read-only cockpit.
- Assignment/admission identity binding beyond current claim binding.

The October 11 basic-security rule targets accidents and untrusted project
content. Same-user adversarial races, cryptographic local attestation and new
confinement are outside this scope. Basic path containment, no secrets, durable
data integrity and provider-normal protections remain required.

The local launch controller may record what it actually observes: the exact
assignment/packet/launch inputs and the child it owns. A create-once record binds
those facts for consistency checks and recovery. It does not authenticate local
files against their owner, establish that an old PID is currently alive, or
establish worker completion merely because a process started or exited. Mocked
processes and tests remain source-only evidence. Actual worker, integrated and
locked-vision evidence still require their respective real observations.

Publication and provider-generation authorizations are separate. This scope
decision grants no retry of a rejected action, new credentials, provider settings,
OS/security changes, or permission to import the legacy runtime.

## Decision provenance

The exact user answers are recorded by CC in the established project mailbox;
this document cites that handoff rather than claiming independent authentication
of the originating chat.

- `20261016-opus-oren-port-list-decision.md`, SHA256
  `f3c7b4a4c7c5100a9ffcb2fdaabf182289e71d566be3157d51d216203abb27d7`.
- `20261011-233000-claude-OWNER-RULE-security-and-process.md`, containing Oren's
  verbatim **"only on basic level"** rule.
- `20261017-101144-opus-scope-receipts.md`, reconciling the current local-provenance
  interpretation and correcting the earlier claim that legacy had no callable
  first-turn route. Legacy source has such a route; it is not the current bar.

The October 17 follow-on branch records this decision next to the retained audit.
The previously qualified `ec06f019` source tree and its frozen review packets
remain unchanged.
