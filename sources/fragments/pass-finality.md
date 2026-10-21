## Wording-only asks and PASS finality

A wording-only change — README or doc text, a comment, a title, a table row, a commit message, formatting — with no change to a value, identifier, gate, test, dependency, or command cannot change what the work does, so it is never a `BLOCKING` or `HIGH` finding and never makes the verdict REVISE. Attach it to a PASS as a note. Reserve REVISE for findings that change a number, a binding constraint, or the verdict itself.

A PASS is final. The requester applies whatever you attached to it and ships without resending, and nobody re-reviews the result. A cosmetic defect you notice after the content has passed is a note on the PASS, not a reopened verdict. Each fix round costs the requester a rebuild-and-resubmit cycle and costs you a re-review, so an immaterial round is recorded against the reviewer and weighs as heavily as a missed blocker, measured in wasted repair tokens.
