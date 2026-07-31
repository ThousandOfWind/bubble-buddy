<!--
Default issue body template for Bubble Buddy support-skill escalations.

The skill fills this in when it can't resolve a problem or when the user is
giving optimization / feature feedback. Users can override it locally by
creating their own template at ~/.bubble-buddy/issue-template.md (Win:
%USERPROFILE%\.bubble-buddy\issue-template.md); when that file exists the skill
uses it verbatim in place of this one. Keep placeholders in {curly braces} —
the skill replaces the ones it can fill and drops the guidance comments.
-->

### Type
<!-- bug | feature-request | optimization-feedback -->
{issue_type}

### Summary
<!-- One sentence: what's wrong or what the user wants improved. -->
{summary}

### Context
<!-- Filled from the troubleshooting session; never include secrets/keys. -->
- Bubble Buddy version: {app_version}
- OS / arch: {os_arch}
- Backend / polish: {backend} / {polish} ({polish_engine})
- Install edition: {edition}

### What the user did / what happened
<!-- Steps to reproduce, or the situation that prompted the feedback. -->
{what_happened}

### Exact error text or quoted UI message (if any)
<!-- Paste from tray -> Copy diagnostics. Redact any Azure key. -->
```
{error_text}
```

### What the user wants (request)
<!-- The user's actual ask, in their words. -->
{user_request}

### Expected / proposed fix
<!-- The user's or the agent's idea of a good resolution, if there is one. -->
{expected_fix}

### Diagnostics log tail (optional)
<!-- Last relevant lines of ~/.bubble-buddy/logs/bubble-buddy.log. Redact secrets. -->
```
{log_tail}
```

<!-- Filed with help from the Bubble Buddy support skill. -->
