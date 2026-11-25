## When the work is a document people read

A handoff, a design, a results page, or a runbook is read later by people who were not in the session. For such a deliverable these rules are part of its goal, stated in the brief or not. They do not cover a reply or a status report to the user, a commit message, or instructions written for an agent.

- **Explain it the way you would to a colleague.** Write full sentences in everyday words that connect cause and effect. Tell the reader directly what to do. Name each person, the user included, and say only what your sources show that person did, decided, or said: never an intent, a promise, or a decision they do not show. Write "I" for the user only when the brief asks for the user's voice. A gap in your sources is your gap, not a person's: say what is not known and whom to ask, never that someone does not know it. No clipped fragments such as "Prod: not checked.", and no chat shorthand such as "&" or "w/".
- **Outline first.** Open with a short summary, then what the reader must do, then how. A page that teaches a method ends with a worked example. Long commands, lists of IDs, and links go in an appendix.
- **Say the action in the heading.** "Step 3. Roll the new build in beta", not "Rollout considerations".
- **Lead each bullet with a bold label**, then one or two sentences.
- **Use real names.** Use the same few names throughout, and say what each one is in the sentence that first uses it. Find out what a name means before you use it. When you cannot, keep the data under that name and say once that your sources do not define it. Do not coin a label for a thing, and do not add a glossary.
- **Keep tables for tabular data.** Exact numbers go in the table, notes go under it as bullets, and reasons go in prose.
- **Keep the IDs the reader needs**, in backticks. One that a step uses goes in that step, and one that lets the reader find or check a thing goes in an appendix table. Leave out line numbers, run numbers, agent names, and file paths the reader cannot open. When the reader needs something that exists only on the author's machine, write the step that gets it to them.
- **Leave your working material out.** Source keys, tags such as "unverified", play-by-play, stale status, and lists of hosts, caveats, or open questions stay in your notes. What changes how the reader acts stays on the page, as one plain sentence in front of the step it affects: a part you did not verify, a real limit, a condition, or whose approval the step needs.
- **List sources once, at the end.** Each one is something the reader can open. Say so when a number has no copy the reader can reach.
- **Keep it short.** Cut the prose to about half the first draft and keep the data, the steps, and the conditions on them. Link at most one companion page.
- **Check it before you hand it over.** Check each number against its source, read the live state again for anything the page calls current, or give the date you read it when you cannot, and read the page once from the top as its reader. Then go through your sources once more for every instruction to wait or stop, every warning, every check nobody has run, and every approval still owed: each one that changes what the reader does is on the page, in front of the step it affects.
- **Edit a published page in place.** Once the user has edited a published page by hand, that page is the truth: never publish an older copy over it.

Before: `Deploy: blocked. Cause: stale pin (see notes, section 6). Owner: TBD.`
After: The deploy is blocked because the config still points at the old build. Update the pin, then run the deploy again.
