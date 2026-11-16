I am working on a leetcode notes generation app, treat me as a new intern joinee started to work on this project, you be my mentor and
help me understand the pipeline django app of the project rest of the apps I understood and there's no worries the pipeline is the main
part of it and its a bit complex, so I want you to go through the pipeline app and understand the whole workflow, how things are connected
and teach/instruct me to open the file side by side as you explain through how the code works in that file. work me bottom up through the
dependency graph in the order of a file which has no dependency of another file or code(app related logic) like models.py and up we go
till the top which is kindof like a orchestrator which uses all the other files to create a flow. lets go file by file and help me
understand in depth of how its connected. dont give all the reponses at once I may have quetsions after you tell me about a file so only
after Im satisfied with the understand I'll tell to proceed to next file

○ Thought for 17.1s (The file structure shows distinct areas: model definitions, stage definitions/handling, runtime utilities (locking,...)
● Ran 3 commands (find . -maxdepth 3 -... · find pipeline -type ... · python3 -c " import ...)
● Explored 2 files (definitions.py, models.py) (ctrl+o to expand)

▸ Thought for 11s
Welcome to the team! I'm glad you're diving into the pipeline app. Since you're already familiar with the other apps (like leetcode,
problems, prefill, and notes), understanding the pipeline is the final piece of the puzzle.
Think of pipeline as the central conveyor belt: it takes problems, fetches their LeetCode data & assets, asks the LLM to prefill study
notes, pauses for user review if needed, and writes formatted markdown notes to disk—all while handling pacing, rate limits, errors,
locks, and recovery.
──────

### Our Bottom-Up Learning Roadmap

To build your mental model without getting overwhelmed, here is the dependency graph we will climb together:

1. Foundations (Zero internal dependencies):
   • definitions.py (We start here!)
2. Data Model Layer:
   • models.py (Task, TaskItem, ItemStage)
3. Stage Contract & Dependencies:
   • outcomes.py
   • deps.py
4. Task Configuration & Planning:
   • settings.py
   • plan.py
   • transitions.py
5. Stage Handlers & Registry:
   • fetch.py
   • notes.py
   • registry.py
6. Task Creation & Validation:
   • eligibility.py
   • creation.py
   • preview.py
   • summary.py
7. Execution Engine & Runtime:
   • engine.py
   • locking.py
   • pacing.py
   • recovery.py
   • worker.py
8. Top-Level Orchestration & Interfaces:
   • control.py
   • actions.py
   • views.py
   ──────

### Step 1: Open definitions.py

Go ahead and open definitions.py in your editor beside this chat.
Notice that this file has zero dependencies on anything else in our codebase—it only imports models from standard django.db.
"""The stages every problem goes through in a task, in order."""

    from django.db import models


    class Stage(models.TextChoices):
        # The label is the text shown in the "working on" column.
        FETCH_PROBLEM = "fetch_problem", "Fetching problem"
        FETCH_IMAGES = "fetch_images", "Fetching images"
        FETCH_SUBMISSION = "fetch_submission", "Fetching submission"
        GENERATE_PREFILL = "generate_prefill", "Generating prefill"
        REVIEW = "review", "Waiting for review"
        SAVE_NOTES = "save_notes", "Saving notes"


    STAGE_ORDER: tuple[Stage, ...] = tuple(Stage)


    def stage_order(stage: str) -> int:
        """Where a stage sits in the pipeline: 0 for the first."""
        return [candidate.value for candidate in STAGE_ORDER].index(stage)


    def next_stage(stage: str) -> Stage | None:
        """The stage after this one, or None after the last."""
        position = stage_order(stage) + 1
        return STAGE_ORDER[position] if position < len(STAGE_ORDER) else None

#### Why this file exists and how it works:

1. Stage Enum (models.TextChoices):
   Every problem being processed by a task passes through a strict, linear lifecycle:
   • FETCH_PROBLEM: Fetches the problem statement and metadata from LeetCode.
   • FETCH_IMAGES: Downloads any images embedded inside the problem description so they can be viewed offline.
   • FETCH_SUBMISSION: Retrieves the user's latest accepted code submission from LeetCode.
   • GENERATE_PREFILL: Prompts the AI provider (Claude, OpenAI, Gemini, etc.) to analyze the problem and submission, generating initial
   notes.
   • REVIEW: A human-in-the-loop stage. If review is enabled, the pipeline halts this item here and waits for you to approve or edit
   the AI draft in the UI.
   • SAVE_NOTES: Writes the final Markdown note file into the repository's notes directory.
2. **STAGE_ORDER**:
   This preserves the fixed tuple order of all members of Stage. In Python's enum implementation, iterating an enum yields members in
   declaration order.
3. **stage_order(stage)**:
   Given a stage's string value (like "fetch_problem"), it returns its 0-indexed integer position (0, 1, 2, ...). We use this to sort
   stages and store their numerical index in the database.
4. **next_stage(stage)**:
   Determines what stage comes immediately after the current one, or returns None if we've reached the end (SAVE_NOTES). The execution
   engine uses this to step an item forward through the pipeline.
   ──────
   Take a look through definitions.py. Do you have any questions about these 6 stages, why they're in this order, or how the lookup
   functions work?

Once you're comfortable, give me the nod and we'll move right up to models.py!
────────────────────────────────────────────────────────────

> I think I'm good with this one, its straight forward lets move on to the next one

▸ Thought for 11s

Great! Now let's move up one level to the database models that hold all state for the pipeline.

### Step 2: Open models.py

Open models.py in your editor.

This file only depends on two things:

1. Standard Django model tools (django.db.models).
2. Our foundational Stage enum that we just reviewed, plus Problem from the problems app.

Here you will find three models organized in a strict 1-to-many hierarchy:
Task (1)
└── TaskItem (Many: one per Problem in the task)
└── ItemStage (Many: up to 6 rows per Item, one for each Stage)

Let's break down each model:
──────

#### 1. Task

Represents a single "batch job" or run triggered by a user (for example: "Fetch and generate notes for my 10 solved Dynamic Programming
problems").
Key fields:

• status: Tracks the overall batch state using Task.Status:
• ACTIVE: Worker can pick up its problems.
• PAUSED: Execution paused (either explicitly by the user or automatically due to an unhandled error).
• CANCELLED: User aborted the batch.
• FINISHED: Every problem in this batch has reached a terminal state.
• pause_reason: A text explanation if the task was paused by an error.
• settings: A JSON snapshot of the user's config at the exact moment the task was created.
│ Architecture tip: Storing this snapshot ensures reproducibility. If you start a 50-problem task and later change your global AI
│ provider or pacing settings in user config, that running task's behavior won't change mid-flight.
──────

#### 2. TaskItem

Represents one problem being processed within a specific Task.

Key fields:

• task & problem: Links the batch task to the problem catalog.
• Notice the constraint at line 81: UniqueConstraint(fields=["task", "problem"]) prevents adding the same problem twice to the same
task.
• position: An integer sorting order so items run in the user's intended sequence.
• status: State of this problem (QUEUED, RUNNING, NEEDS_INPUT, COMPLETED, FAILED).
• stage: Which Stage the item is currently on or blocked at (e.g., FETCH_IMAGES, REVIEW).
• input_kind: When status == "needs_input", this tells the UI what user action is required:
• MANUAL_PREFILL: In case automated AI calls are disabled or failed and manual prompt/response copy-paste is needed.
• REVIEW: The draft note was generated and is waiting for you to review and approve in the UI before saving.

──────

#### 3. ItemStage

Represents the individual lifecycle step for an item. If a TaskItem represents "Two Sum", it will have up to 6 ItemStage rows—one for
FETCH_PROBLEM, one for FETCH_IMAGES, etc.

Key fields:

• item: Foreign key back to TaskItem.
• stage: The Stage enum value.
• order: Derived from stage_order(stage) (0 to 5).
• status:
• PENDING: Hasn't started yet.
• RUNNING: Currently being executed by the background worker.
• DONE: Completed successfully.
• SKIPPED: Decided by planning/handlers that this step wasn't needed (e.g., problem description is already on disk, or the user
disabled the review stage).
• WAITING: Blocked waiting for user input.
• FAILED: Errored out.
• detail: A quick human-readable reason (e.g. "already fetched", "review turned off").
• Unique constraint: [item, stage] guarantees each item has at most one record per stage.
──────

### How this connects to the bigger picture

When you create a task:

1. A Task is created with its snapshot settings.
2. A TaskItem is created for each selected problem.
3. For each TaskItem, a set of ItemStage records is pre-created in PENDING status.
4. As the background worker advances, it updates both ItemStage and TaskItem until all reach COMPLETED or SKIPPED.
   Have a look through models.py. Does the hierarchy and the distinction between TaskItem.status vs ItemStage.status make sense? Feel free
   to ask any questions!
   ────────────────────────────────────────────────────────────

> yes I understood the models it makes sense to me let move on to the next
> ● Explored 2 files (outcomes.py, deps.py) (ctrl+o to expand)

▸ Thought for 8s
Awesome. Now that we have the models in place, let's look at the contract between the execution engine and the code that actually runs
each stage.

### Step 3: Open outcomes.py

Open outcomes.py side by side.
When an individual stage handler runs (like fetching problem details, downloading images, calling an AI model, or saving notes), it
needs a clean, standard way to tell the engine:

1. "I finished successfully."
2. "I didn't need to do any work, so I skipped."
3. "I need human input before proceeding."
4. "I hit an error specific to this problem."
5. "I hit a catastrophic error that affects the whole task, so pause everything."
   That protocol is defined entirely in this 38-line file.
   ──────

#### 1. Exceptions for Failures

Lines 10–15 define two distinct exception types:

    class StageError(Exception):
        """The stage failed for this problem. str(exc) is the message shown to you."""


    class PauseTask(Exception):
        """Nothing more in this task can run until something is fixed. str(exc) says what."""

• **StageError**:
Used when something goes wrong with this specific problem only (e.g., this problem has no accepted submission on LeetCode, or LeetCode
returned a 404 for this slug). The engine marks this TaskItem as FAILED, saves the error message, but continues processing the next
problem in the batch.
• **PauseTask**:
Used when something goes wrong that invalidates the entire task run (e.g., missing or expired LeetCode session cookie, invalid AI API
key, or network down). The engine catches this, sets the entire Task.status to PAUSED, records the reason, and stops the worker so it
doesn't fail 50 items in a row.
──────

#### 2. The Result Dataclass: StageOutcome

    @dataclass(frozen=True)
    class StageOutcome:
        status: str  # ItemStage.Status: DONE, WAITING or SKIPPED
        detail: str = ""
        input_kind: str = ""  # what the item is waiting for, when WAITING

Notice that StageOutcome is frozen=True (immutable). It returns:

• status: Uses the constants from ItemStage.Status (DONE, SKIPPED, or WAITING).
• detail: A brief note saved to the DB (e.g. "downloaded 3 images" or "already fetched").
• input_kind: If status is WAITING, this tells the engine whether the item is waiting for "review" or "manual_prefill".
──────

#### 3. Convenience Constructor Functions

Lines 25–38 provide clean helper factories so handlers don't have to instantiate StageOutcome manually:

• clip(text, limit=300):
Safely trims detail to 300 characters (with an ellipsis …) so it never exceeds the ItemStage.detail CharField(max_length=300) database
column.
• done(detail=""):
Returns a StageOutcome with status=DONE.
• skipped(detail):
Returns a StageOutcome with status=SKIPPED and requires a reason string.
• waiting(input_kind, detail=""):
Returns a StageOutcome with status=WAITING and sets input_kind so the engine knows what input is expected.
──────

### How this connects:

When we write stage handlers in upcoming files (like fetch.py or notes.py), they will look something like this:
if problem.content_html:
return skipped("already fetched")
...
return done("fetched problem description")

And if credentials are missing:
raise PauseTask("LeetCode session cookie expired")

Does this outcome and error handling pattern make sense? Any questions on StageError vs PauseTask or the outcome helpers before we move
to the next file?

────────────────────────────────────────────────────────────

> yes I understood this cleanly lets move onto the next
> ▸ Thought for 10s
> Excellent. Let's look at the other small foundational file in this directory:

### Step 4: Open deps.py

Open deps.py in your editor.

    """What a stage can use from the outside world, and how it loads the problem it works on."""

    from collections.abc import Callable
    from dataclasses import dataclass

    from leetcode.bootstrap import get_client, get_image_downloader
    from leetcode.client import LeetCodeClient
    from leetcode.images import ImageDownloader
    from prefill.providers.base import Provider
    from prefill.providers.registry import get_provider
    from problems.models import Problem

    from ..models import TaskItem


    @dataclass(frozen=True)
    class StageDeps:
        """The outside world a stage can use. Tests swap these for fakes."""

        get_client: Callable[[], LeetCodeClient] = get_client
        get_downloader: Callable[[], ImageDownloader] = get_image_downloader
        get_provider: Callable[[str], Provider] = get_provider


    def current_problem(item: TaskItem) -> Problem:
        """Load the problem again, so a stage sees what the stages before it saved."""
        return Problem.objects.get(pk=item.problem_id)

This file is only 27 lines, but it solves two very important architectural needs: Dependency Injection and Database Freshness.
──────

#### 1. StageDeps: Dependency Injection

When a stage executes, it needs to reach out to the external world:

• Fetching submission or problem metadata requires LeetCodeClient.
• Downloading images requires ImageDownloader.
• Generating prefill notes requires an AI Provider (e.g. Claude, Gemini, OpenAI).

Instead of hard-coding imports and calls to external APIs directly inside each stage handler, we wrap these factory functions into the
StageDeps dataclass with sensible defaults:

• get_client: calls leetcode.bootstrap.get_client
• get_downloader: calls leetcode.bootstrap.get_image_downloader
• get_provider: calls prefill.providers.registry.get_provider
Why this is great for testing:
In test suites, instead of monkey-patching global variables or intercepting network sockets, tests simply pass a custom
StageDeps(get_client=fake_client_fn, ...) into the pipeline engine.
──────

#### 2. current_problem(item): Avoiding Stale Data

Notice this helper function:
def current_problem(item: TaskItem) -> Problem:
"""Load the problem again, so a stage sees what the stages before it saved."""
return Problem.objects.get(pk=item.problem_id)

In Django, if you access item.problem, Django caches that Problem object in memory on the item instance.
However, in our pipeline:

1. FETCH_PROBLEM runs and writes content_html into the database.
2. FETCH_IMAGES runs and downloads image assets, updating image paths.
3. Later, GENERATE_PREFILL runs and needs the problem's HTML content and submission code to send to the LLM.
   If GENERATE_PREFILL used item.problem, it would read the old, stale in-memory data from before FETCH_PROBLEM ran!
   Calling current_problem(item) ensures each stage gets the fresh, freshly-committed database row.
   ──────
   Take a look at deps.py. How does this feel to you? Any questions on dependency injection or database caching here? If all looks clear,
   let me know and we will move to the next file!
   ────────────────────────────────────────────────────────────

> Okay I have some doubts, we only created this StageDeps for the easy test writing right we could have also just imported these functions
> directly where it was needed as well right? I understand that for writing test cases if we didn't have this StageDeps then we'd have to
> mock these functions whereever in the pipline engine they call, but having this what we can do is just directly mock this stageDeps with
> our mocked values, and they will be used througout the tests in the pipeline whereever its need right?

○ Thought for 8.9s (Initial hypothesis: `StageDeps` simplifies testing by providing a centralized point for mocking dependencies within...)
● Ran (git grep -n "StageDeps" pipeline/) (ctrl+o to expand)
Spot on! You hit the nail right on the head. 🎯

### You are 100% correct on both counts:

1. Could we have imported them directly?
   Yes, absolutely. In standard Python, you could have imported get_client, get_image_downloader, and get_provider right inside fetch.py
   and notes.py. In production, the code would run exactly the same way.
2. Why do it this way instead?
   If you import them directly, every single test that runs the pipeline or a stage handler would need something like: # The brittle, monkey-patching way:
   @patch("pipeline.stages.handlers.notes.get_provider")
   @patch("pipeline.stages.handlers.fetch.get_client")
   def test_pipeline_run(mock_client, mock_provider):
   ...
   The downsides of monkey-patching in large projects:
   • Brittle paths: If someone refactors or moves get_provider to a different subfolder, string-based @patch("path.to.thing") breaks at
   runtime without type-checker warnings.
   • Test leakage: If a mock isn't cleaned up properly after an exception, it can leak into other tests running in the same process.
   • Stacking decorators: When testing an end-to-end flow with the engine or worker, you'd end up stacking 4–5 @patch decorators on
   every single test method.
3. How StageDeps solves this:
   Notice lines 42–47 in worker.py:42-47 and line 132 in engine.py:132:
   def run_task(task, deps: StageDeps | None = None):
   deps = deps or StageDeps() # Defaults to the real production implementations!
   When the app runs in production, deps is omitted and defaults to the real network clients.But in tests (take a look at
   test_note_handlers.py:52), writing a test is as simple and type-safe as:
   deps = StageDeps(get_provider=lambda name: FakeProvider())
   run_stage(item, row, deps)
   No magic, no monkey-patching, perfectly isolated.
   ──────
   That was a great architectural question. Does that give you the full context on why we use that pattern here?
   If you're ready, let's take our next step to:
   settings.py — which manages all the configurable switches and parameters for tasks!

────────────────────────────────────────────────────────────

> yes I think Im ready for the next one lets goo!

● Explored 1 file (settings.py) (ctrl+o to expand)

▸ Thought for 8s
Love the energy! Let's dive in.

### Step 5: Open settings.py

Earlier, when we looked at models.py, you noticed the field on Task:
settings = models.JSONField(default=dict)
This file, settings.py, defines what lives inside that JSON field, how it is typed, how defaults are loaded, and how user overrides are
validated.
──────

#### 1. The Pydantic Schema: TaskSettings

    class TaskSettings(BaseModel):
        model_config = ConfigDict(
            extra="ignore"
        )  # snapshots saved by an older version still load

        provider: str = MANUAL_PROVIDER  # a provider name, or "manual"
        review: bool = True  # wait for your approval before saving notes
        output_dirs: list[str] = Field(default_factory=list)
        replace_edited: bool = False  # replace notes you edited (backed up first)
        refetch: bool = False  # fetch data again even if the problem already has it
        regenerate_prefill: bool = (
            False  # ask the AI again even if there is a current AI note
        )
        manual_fallback: bool = (
            True  # if the AI tool fails, ask you to paste a reply instead
        )

Look at how powerful this configuration schema is:
• extra="ignore": Great backwards compatibility. If the app updates and an older task in the database has retired fields in its JSON,
loading it won't crash.
• provider: The name of the AI service to run ("gemini", "claude", "openai", etc.) or "manual" if you prefer not to use an automated API.
• review: When True, the pipeline pauses at the REVIEW stage for human review. When False, it saves notes straight to disk automatically.
• output_dirs: Target folders where the generated note markdown files should be written (e.g., your Obsidian vault or local notes repo).
• replace_edited: Protects your handwritten notes! If you already manually edited a note file on disk, the pipeline won't overwrite it
unless this flag is True.
• refetch & regenerate_prefill: Caching controls. If a problem was already fetched or note draft was already generated, should the
pipeline reuse the existing data or do it fresh?
• manual_fallback: If the AI provider fails (e.g. rate limit, invalid key, or network issue), rather than crashing the item, it falls
back to asking you to paste the AI prompt's response manually.
──────

#### 2. Resolving Settings at Task Creation

When a user creates a new task (from UI or API), three functions handle merging and validation:

1. **default_task_settings()**:
   Loads the user's global configuration from the userconfig app:
   • Gets default AI provider name.
   • Checks if the user prefers review before writing by default (load_config().review_before_writing).
   • Retrieves default note folders (effective.output_dirs()).
2. **check_override_names(overrides) & check_provider(name)**:
   Strict gatekeepers. If someone passes an unknown setting name (e.g. a typo like {"reviiw": False}), it raises TaskSettingsError. It also
   verifies the provider name exists in the registered providers.
3. **settings_with_overrides(overrides)**:
   Merges: defaults + overrides ➡️ validates against Pydantic schema ➡️ returns the finalized TaskSettings.
   When a task is saved in the database, its .settings field gets settings.model_dump().
   ──────

#### 3. Reading Saved Settings During Execution

When the background worker picks up a task, it calls:

    def task_settings(task: Task) -> TaskSettings:
        """The snapshot saved on a task."""
        return TaskSettings.model_validate(task.settings)

Lines 73–75: This converts the raw database JSON dictionary back into a fully-typed TaskSettings object with autocomplete, types, and
defaults.
──────

### Why this design matters:

Stage handlers never have to guess what user options apply or read global config on the fly. Every handler receives (item, settings,
deps) where settings is an immutable, validated snapshot of the task's rules.

Take a look through settings.py. How do the options like review, refetch, and manual_fallback feel? Any questions here before we see how
these settings are used to build the execution plan in plan.py?

────────────────────────────────────────────────────────────

> I understood this one, the explanation and notes regarding funcitons are really upto point when I tired to read the code directly it
> didn't make much sense to me lets goo to the next one
> ● Explored 1 file (plan.py) (ctrl+o to expand)

▸ Thought for 11s

That's the spirit! Now we're getting into the real "smarts" of the system.

### Step 6: Open plan.py

Imagine you select 20 problems to generate notes for.

• 5 of them you've never processed before.
• 10 of them you processed yesterday, and neither the problem statement nor your solution code changed.
• 5 of them you solved with a new, faster solution since you last generated notes.
If the pipeline blindly fetched everything from LeetCode and called Claude/OpenAI for all 20 problems every time:

1. You'd hit LeetCode rate limits.
2. You'd waste money and tokens on AI calls for unchanged problems.
3. You'd be asked to re-review notes you already approved!

**plan.py** is the brain that prevents this. Before a task even starts running, it analyzes each problem and determines which stages can
start out SKIPPED and which ones must be PENDING.
──────

#### 1. The Core Data Structures

Lines 21–38:

    @dataclass(frozen=True)
    class StageStart:
        status: str      # ItemStage.Status: PENDING or SKIPPED
        detail: str = "" # e.g. "already fetched", "review is off"

        @property
        def skipped(self) -> bool:
            return self.status == ItemStage.Status.SKIPPED

Each stage starts as either:
• PENDING: Needs to be executed by the worker.
• SKIPPED: Already done or disabled, with a human-readable reason in detail.
──────

#### 2. Detecting Stale vs Fresh AI Notes: AiNoteState

Lines 40–72 contain a clever hashing mechanism:

    class AiNoteState(StrEnum):
        NONE = "none"    # no active AI note
        FRESH = "fresh"  # note matches the current problem & code
        STALE = "stale"  # code or problem description changed since note was generated

In ai_note_state(problem):

1. It looks up the existing AIResponse for this problem.
2. It builds the prompt from the current problem and code to calculate an input_hash.
3. If response.input_hash == current_hash: the note is FRESH!
4. If the hash differs (e.g. you updated your solution code): the note is STALE and needs regeneration.
   ──────

#### 3. The Stage-by-Stage Planning Rules

Let's look at how each stage is evaluated (lines 78–122):

1. **plan_fetch_problem**:
   • If problem content is already synced and settings.refetch is False ➡️ SKIPPED ("already fetched").
   • Otherwise ➡️ PENDING.
2. **plan_fetch_images**:
   • Notice the dependency: images are only skipped if images are already downloaded AND fetch_problem was skipped. If the problem HTML
   was re-fetched, new image URLs might be present, so images must run too!
3. **plan_fetch_submission**:
   • If the user specifically "pinned" a preferred submission solution ➡️ SKIPPED ("pinned solution kept").
   • If already downloaded and not refetch ➡️ SKIPPED ("already fetched").
4. **plan_generate_prefill**:
   • If settings.regenerate_prefill is True OR data changed (we re-fetched problem/submission) ➡️ PENDING.
   • Else check note state:
   • If FRESH ➡️ SKIPPED ("AI note already exists").
   • If STALE ➡️ PENDING ("the solution changed since the AI note was made").

5. **plan_review**:
   • If user turned off review (settings.review=False) ➡️ SKIPPED ("review is off").
   • If generate_prefill was skipped because an approved note already exists ➡️ SKIPPED ("existing AI note").
   • Only PENDING if a brand new prefill draft was actually generated!
6. SAVE_NOTES:
   • Always starts PENDING. The note writer on disk will check file timestamps and checksums to decide if the .md file needs writing.

──────

#### 4. The Orchestrator: initial_stages(problem, settings)

This ties it all together into a clean dictionary:

    {
        Stage.FETCH_PROBLEM: StageStart(status="skipped", detail="already fetched"),
        Stage.FETCH_IMAGES: StageStart(status="skipped", detail="already fetched"),
        Stage.FETCH_SUBMISSION: StageStart(status="skipped", detail="already fetched"),
        Stage.GENERATE_PREFILL: StageStart(status="pending", detail="the solution changed since the AI note was made"),
        Stage.REVIEW: StageStart(status="pending"),
        Stage.SAVE_NOTES: StageStart(status="pending"),
    }

When a new TaskItem is created in the database, this dictionary determines the starting status and detail of every ItemStage row.
──────
Take a look at how clean these pure functions are: no side effects, just clear business rules determining what needs to run.

What do you think of how plan_generate_prefill and plan_review decide whether to skip? Does the hash checking make sense? Any questions
before we move to the next file?
────────────────────────────────────────────────────────────

> I dont understand this part
> In ai_note_state(problem):

    1. It looks up the existing AIResponse for this problem.
    2. It builds the prompt from the current problem and code to calculate an input_hash.
    3. If response.input_hash == current_hash: the note is FRESH!
    4. If the hash differs (e.g. you updated your solution code): the note is STALE and needs regeneration.

do we generate the hash from the prompt or the response note from the ai?

○ Thought for 21.2s (The hash is generated from the combined prompt and code, serving as a fingerprint for the input. This hash is then...)
● Ran (git grep -n "input_hash" prefill/)
● Explored 2 files (prompt_builder.py, models.py) (ctrl+o to expand)
▸ Thought for 9s

That is an insightful question, and the name input_hash gives us the biggest clue!
It is generated from the inputs fed into the AI (the problem and your solution code), not the AI's response.
Here is why, and how it works:
──────

### Why we hash the inputs instead of the response

When you already have an AI note saved in the database, the question the pipeline needs to answer isn't "what did the AI say?" (we
already have that saved in AIResponse.raw_text).
The question is:

│ "Is this existing AI note still valid for what the problem and code look like right now?"

Suppose you solved Two Sum in Python 3 weeks ago with an O(N²) brute force solution. The AI generated a note explaining that brute force
logic.

Yesterday, you went back to LeetCode, wrote an optimal O(N) hash map solution, and synced it. If we didn't check whether the inputs
changed, the pipeline would say "Oh, an AI note exists, skip!" and your note file would still explain your old brute force code instead
of your optimal solution!
──────

### How the hash is calculated

If you look inside prompt_builder.py:79-81:

    input_hash = hashlib.sha256(
        "\0".join([statement, submission.lang, code]).encode()
    ).hexdigest()

It hashes three things together:

1. statement: The problem description text.
2. submission.lang: The programming language (e.g. python3, cpp, rust).
3. code: Your actual submitted code.
   ──────

### The comparison in plan.py:62-72

When the note was first created, that SHA-256 hash was saved in the database in AIResponse.input_hash.
When plan.py runs today:

1. It calculates current_hash from the problem's current description and latest code.
2. It compares response.input_hash == current_hash:
   • Equal (FRESH): Your code and problem text haven't changed by even a single character. The existing note is 100% accurate. Skip
   prefill!
   • Not Equal (STALE): You changed your code or solved it in a new way. The existing note no longer matches your code. Run prefill
   again!
   Does that make the purpose and calculation of input_hash click into place?

────────────────────────────────────────────────────────────

> yess now it makes a lot of sense, rest of it I understood lets move on
> ● Explored 1 file (transitions.py) (ctrl+o to expand)
> ▸ Thought for 15s

Now let's look at the state machine of our pipeline:

### Step 7: Open transitions.py

Take a look at the opening docstring of this file:
│ "How the statuses of a task, an item and its stages change. Nothing in here does any work."

This file is pure state transitions. It doesn't talk to the network, doesn't generate prompts, and doesn't write files. Its sole job is
to move Task, TaskItem, and ItemStage from one status to another in the database accurately and safely.
──────

#### 1. The Helper: save_fields(obj, **values)

    def save_fields(obj, **values) -> None:
        """Set these fields on a model instance and save only them."""
        for name, value in values.items():
            setattr(obj, name, value)
        fields = list(values)
        if hasattr(obj, "updated_at"):
            fields.append("updated_at")  # auto_now only updates when it is listed
        obj.save(update_fields=fields)

Why this helper matters:
In Django, calling a bare obj.save() writes every single column back to the database. If a background worker and a web request modify
the same item concurrently, a bare .save() can accidentally overwrite the other process's changes.
By using update_fields=[...], Django generates an exact SQL statement like:

    UPDATE pipeline_taskitem SET status = 'running', stage = 'fetch_problem', updated_at = NOW() WHERE id = 42;

It only touches the exact columns that changed.
──────

#### 2. Item & Stage Lifecycle Transitions

Follow the lifecycle of an item as it executes:

1. **begin_stage(item, row)**:
   • When an item starts a stage:
   • row.status ➡️ RUNNING (records started_at).
   • item.status ➡️ RUNNING, sets stage=row.stage.
   • If task.started_at wasn't set yet, sets it now.
2. **apply_outcome(item, row, outcome)**:
   • When a stage handler finishes returning a StageOutcome:
   • If outcome.status == WAITING: calls park_item(...).
   • row.status ➡️ WAITING.
   • item.status ➡️ NEEDS_INPUT, sets input_kind ("review" or "manual_prefill").
   • The item is now safely "parked", and the worker can continue with other problems!
   • Otherwise: calls complete_stage(...) (DONE or SKIPPED, sets finished_at).
3. **requeue_item(item, next_stage)**:
   • If the stage finished and there is another stage next:
   • item.status ➡️ QUEUED, item.stage ➡️ next_stage.
   • Ready for the worker to pick up the next stage.
4. **complete_item(item)**:
   • All stages for this problem have reached DONE or SKIPPED.
   • item.status ➡️ COMPLETED (sets finished_at).
5. **fail_stage(item, row, message)**:
   • A problem-specific error occurred (StageError).
   • row.status ➡️ FAILED, item.status ➡️ FAILED.
   • Stores the error string so the UI can display why this problem failed.

──────

#### 3. Task-Level Transitions (Pauses and Errors)

What happens if something goes wrong for the whole batch?

• **pause_for_error(item, row, reason)**:
• When a fatal PauseTask error occurs (e.g. cookie expired), we call put_back(item, row).
• This resets row back to PENDING and item back to QUEUED (so it's not marked as failed—it simply didn't run yet!).
• Then calls pause_task(task, reason), setting task.status = PAUSED.
• **refresh_task_status(task)**:
• Checks task_is_done(task) (meaning no items are queued, running, or waiting).
• If all items are COMPLETED or FAILED, sets task.status = FINISHED.
──────
Notice how clean this separation is: the execution engine never does messy database updates directly; it delegates every state change
through these clear functions in transitions.py.
Take a read through transitions.py. How does the state machine flow feel? Any questions before we look at the actual stage workers in
handlers/?

────────────────────────────────────────────────────────────

> I couldn't understand the begin_stage we do stage=row.stage does it contain the updated value? like we just updated the row.stage in the
> previous line

requeue_item is also is kinda hard to say like I do understand its upadting the taskitem to queued for the next stage, I think I would
appreciate or undertsand more when I get to know how the worker or orchestrator engine picks up work to complete and all, I think only
then it would be clear to me why we have it this way, by the looks it seem the worker see the taskItem's stage and its status to decide
which is the next stage to be done

rest I think I understood well

○ Thought for 8.8s (The `begin_stage` function's purpose is to mark the start of a stage, but it doesn't directly update the `row.stage`...)
● Ran (git grep -n "next_item" pipeline/) failed
● Explored 1 file (engine.py) (ctrl+o to expand)

▸ Thought for 7s
These are fantastic observations! You're thinking like a distributed systems engineer. Let's clear up both of those points:
──────

### 1. begin_stage: What is row.stage vs item.stage?

Take a closer look at lines 27–34 of transitions.py:27-34:

        save_fields(
            row, status=ItemStage.Status.RUNNING, started_at=now, finished_at=None, error=""
        )
        save_fields(
            item,
            status=TaskItem.Status.RUNNING,
            stage=row.stage,
            ...
        )

Notice what is happening:
• In line 28, we updated row.status (from PENDING to RUNNING). We did not update row.stage.
• Remember from models.py:91: an ItemStage row represents one specific stage (for example, there is a fixed row in the DB where row.
stage = "fetch_problem", and another row where row.stage = "fetch_images"). That value never changes.
• But the TaskItem model also has a stage column. That column is displayed directly on the UI dashboard under the "Working on" column.
• So stage=row.stage simply copies that name over to item.stage so the UI (and logs) immediately show: "Problem 'Two Sum' is now running
'fetch_problem'".
──────

### 2. Why requeue_item exists (A sneak peek into engine.py)

Your intuition is 100% spot-on! Let me show you how the engine uses this.
Our background worker runs an event loop. In engine.py:57-77, whenever the worker is ready for work, it asks the database:

    def next_queued_item() -> TaskItem | None:
        """The next item to work on: oldest active task first, then its first queued item."""
        return (
            TaskItem.objects.filter(status="queued", task__status="active")
            .order_by("task__created_at", "position")
            .first()
        )

Now, trace what happens when an item runs through stages:

    [QUEUED] ──(worker picks it up)──> [RUNNING: Stage 1: FETCH_PROBLEM]
                                                 │
                                         Stage 1 succeeds!
                                                 │
                                      Is there a Stage 2?
                                        ├── YES ──> requeue_item(item, next_stage="fetch_images")
                                        │           Status goes back to [QUEUED]!
                                        │           Worker will pick it up on the next loop iteration.
                                        │
                                        └── NO  ──> complete_item(item)
                                                    Status becomes [COMPLETED]!

Take a look at how engine.py calls this after a stage finishes:
following = next_pending_stage(item)
if following is None:
complete_item(item)
else:
requeue_item(item, following.stage)

#### Why put it back into the queue instead of running all stages in a tight for loop?

1. Fairness across batch jobs: If Task A has 50 problems, the worker can process stage 1 for Problem 1, stage 1 for Problem 2, etc.,
   allowing progress across items and tasks rather than blocking everything on one giant monolithic loop.
2. Graceful pacing & cancellation: Between each stage, the worker checks: Did the user hit pause or cancel while Stage 1 was running?
   Are we hitting LeetCode rate limits? By requeueing between stages, the system can pause or pace cleanly at any boundary.
   ──────
   Does that make both begin_stage and requeue_item feel crystal clear now?

────────────────────────────────────────────────────────────

> yes now it makes sense lets move on to the next
> ○ Thought for 5.9s (All requested files have been successfully reviewed. The next logical step involves examining the core stage executi...)
> ● Explored 1 file (fetch.py) (ctrl+o to expand)

▸ Thought for 6s

Awesome. Now we arrive at the code that actually does the real work!

### Step 8: Open fetch.py

Open fetch.py in your editor.

This file implements the handlers for our first three pipeline stages:

1. FETCH_PROBLEM
2. FETCH_IMAGES
3. FETCH_SUBMISSION
   Look at how concise this file is—only 34 lines! It brings together all the pieces we've learned so far:

• TaskItem from pipeline.models
• StageDeps and current_problem from pipeline.stages.deps
• StageOutcome, StageError, and done from pipeline.stages.outcomes
• TaskSettings from pipeline.tasks.settings
──────

#### 1. The Standard Handler Signature

Notice that all stage handler functions share the exact same signature:
def handler_name(item: TaskItem, settings: TaskSettings, deps: StageDeps) -> StageOutcome:

Every handler receives:

• The item it is working on.
• The frozen settings for this task run.
• The deps containing the factory functions for LeetCode clients and downloaders.
And it always returns a StageOutcome (or raises an exception).
──────

#### 2. The Core Helper: fetch_part(...)

    def fetch_part(item: TaskItem, deps: StageDeps, part: str) -> StageOutcome:
        """Fetch one part of the problem. LeetCode errors are left for the engine to handle."""
        run_part(current_problem(item), part, deps.get_client(), deps.get_downloader())
        return done()

Look at what this does:

1. Calls current_problem(item) (remember our discussion on deps.py!) so it reloads the problem row from the database to ensure it's not
   stale.
2. Gets the network clients via deps.get_client() and deps.get_downloader().
3. Calls run_part from the problems app, which makes the GraphQL/HTTP request to LeetCode and saves the results directly to the problem
   record.
4. Returns done() (StageOutcome(status="done")).

Notice that it does not catch LeetCode exceptions here (like AuthError or RateLimitError). It lets them bubble straight up to engine.py,
because the engine is responsible for deciding whether an error pauses the whole task or just fails this problem.
──────

#### 3. The Three Stage Handlers

    def fetch_problem(
        item: TaskItem, settings: TaskSettings, deps: StageDeps
    ) -> StageOutcome:
        return fetch_part(item, deps, SyncTask.Part.CONTENT)


    def fetch_images(
        item: TaskItem, settings: TaskSettings, deps: StageDeps
    ) -> StageOutcome:
        if not current_problem(item).raw_question_html:
            raise StageError("the problem's statement hasn't been fetched yet")
        return fetch_part(item, deps, SyncTask.Part.IMAGES)


    def fetch_submission(
        item: TaskItem, settings: TaskSettings, deps: StageDeps
    ) -> StageOutcome:
        return fetch_part(item, deps, SyncTask.Part.SUBMISSIONS)

Look at fetch_images:
Before attempting to download images, it checks if raw_question_html exists on the problem. If someone somehow ran FETCH_IMAGES without
running FETCH_PROBLEM first, it raises:

    raise StageError("the problem's statement hasn't been fetched yet")

Remember from outcomes.py: StageError means "this specific problem cannot proceed", so the engine will mark this TaskItem as FAILED with
that error message.
──────
That's all there is to fetch.py! Everything is clean, modular, and delegates to the problems domain service.

How does this file feel? Ready to look at the next handler file, notes.py, where the AI prompt generation, review parking, and note
writing live?

────────────────────────────────────────────────────────────

> this was easy and simple to understand lets move on

● Explored 1 file (notes.py) (ctrl+o to expand)
▸ Thought for 11s
Now let's examine the second half of the pipeline: generating the AI notes, human review, and writing Markdown files to disk.

### Step 9: Open notes.py

Open notes.py side by side.
This file handles the remaining 3 stages:
• GENERATE_PREFILL
• REVIEW
• SAVE_NOTES
It is also a masterclass in resilient error handling. Let's walk through each stage:
──────

#### 1. Stage 4: GENERATE_PREFILL (lines 23–79)

When the worker arrives at generate_prefill:
def generate_prefill(item, settings, deps) -> StageOutcome:
if settings.provider == MANUAL_PROVIDER:
return ask_for_manual_reply("Paste a reply from your AI chat.")
return generate_with_provider(item, settings, deps)
If the user didn't configure an automated AI provider (or explicitly set provider to "manual"), it immediately calls
ask_for_manual_reply:

• Returns waiting(TaskItem.InputKind.MANUAL_PREFILL, ...).
• Remember our earlier state machine: this sets TaskItem.status = NEEDS_INPUT, parking the item so you can paste the AI chat response
via the UI.
If an AI provider is configured (generate_with_provider):

1. Loads the provider (lines 27–33):
   except ImproperlyConfigured as exc:
   raise PauseTask(f"AI provider problem: {exc}")
   If the API key is missing or invalid in settings, it raises PauseTask. Why pause the whole task? Because if your OpenAI/Anthropic key is
   missing, every other problem in the batch will fail too!
2. Calls AI service (lines 54–65):
   Calls prefill.services.generate_for_problem(...).
3. Resilience with provider_failed (lines 35–49):
   What if the LLM provider times out or returns malformed output?
   • If settings.manual_fallback is True (default): Instead of failing the item, it returns ask_for_manual_reply(...). You can simply
   paste the answer manually!
   • If fallback is off and the service is completely unreachable (ProviderUnavailable), it raises PauseTask.
   • Otherwise, it raises StageError (failing only this problem).
4. On success (line 70):
   Returns done(f"{provider.name} ({model})").
   ──────

#### 2. Stage 5: REVIEW (lines 84–90)

    def review(item: TaskItem, settings: TaskSettings, deps: StageDeps) -> StageOutcome:
        """Wait for your approval. Approving is recorded by the action that marks this stage done."""
        if not AIResponse.objects.filter(
            problem_id=item.problem_id, is_active=True
        ).exists():
            raise StageError("there is no AI note to review")
        return waiting(TaskItem.InputKind.REVIEW, "Approve the AI note before it is saved.")

Look at how straightforward this is:

• It verifies an active AI note draft exists in the database.
• Then it returns waiting(TaskItem.InputKind.REVIEW, ...).
• This parks the item in NEEDS_INPUT (input_kind="review").
• The worker moves on to other items in the batch.
• When you view the note in the Web UI and click "Approve", an action endpoint marks this stage DONE, and the item gets requeued to run
the final stage!
──────

#### 3. Stage 6: SAVE_NOTES (lines 115–125)

    def save_notes(item: TaskItem, settings: TaskSettings, deps: StageDeps) -> StageOutcome:
        if not settings.output_dirs:
            raise PauseTask("No output folder is set. Choose one in Settings.")
        result = generate_notes(
            current_problem(item),
            settings.output_dirs,
            replace_edited=settings.replace_edited,
        )
        if problems := note_problems(result):
            raise StageError("; ".join(problems))
        return done(describe_saved(result))

The grand finale:

1. Validates that the user configured at least one output directory (Obsidian vault, notes directory, etc.). If none is configured,
   raises PauseTask.
2. Calls notes.generation.generate_notes(...) to render and write both the problem note and solution note markdown files.
   • Honors replace_edited: if you edited the note by hand on disk, it creates a backup before replacing, or skips overwriting.
3. Checks for filesystem/permission errors via note_problems(result).
4. Returns done(describe_saved(result)) (e.g. "problem note written; solution note written").
   ──────
   Take a look through notes.py. Pay special attention to how manual_fallback, ask_for_manual_reply, and review use waiting(...) to pause
   execution for user interaction.
   Do you have any questions on how these three stages operate before we check out registry.py to see how handlers are looked up?
   ────────────────────────────────────────────────────────────

> I think I got this one took some time but understood lets move onto the next

● Explored 1 file (registry.py) (ctrl+o to expand)

Take your time—notes.py is where the core domain logic lives, so absorbing it properly is a big milestone!
Now let's see how the engine connects the abstract stage names to the actual functions we just studied.

### Step 10: Open registry.py

Open registry.py in your editor.
This is a clean, 31-line lookup table that glues the Stage enum to the functions in fetch.py and notes.py.

    """Which function runs each stage."""

    from collections.abc import Callable

    from pipeline.models import TaskItem
    from pipeline.stages.definitions import Stage
    from pipeline.stages.handlers.fetch import (
        StageDeps,
        fetch_images,
        fetch_problem,
        fetch_submission,
    )
    from pipeline.stages.handlers.notes import generate_prefill, review, save_notes
    from pipeline.stages.outcomes import StageOutcome
    from pipeline.tasks.settings import TaskSettings

    StageHandler = Callable[[TaskItem, TaskSettings, StageDeps], StageOutcome]

    HANDLERS: dict[Stage, StageHandler] = {
        Stage.FETCH_PROBLEM: fetch_problem,
        Stage.FETCH_IMAGES: fetch_images,
        Stage.FETCH_SUBMISSION: fetch_submission,
        Stage.GENERATE_PREFILL: generate_prefill,
        Stage.REVIEW: review,
        Stage.SAVE_NOTES: save_notes,
    }


    def handler_for(stage: str) -> StageHandler:
        """The handler for a stage, given as a Stage or its name."""
        return HANDLERS[Stage(stage)]

──────

#### Why this pattern is used:

1. StageHandler Type Alias:
   It documents and enforces that every stage handler must strictly match the signature:
   (TaskItem, TaskSettings, StageDeps) -> StageOutcome.
2. Replacing messy if/elif statements with Dispatch:
   Without this dictionary, the engine would have to do: # The messy, error-prone way:
   if row.stage == "fetch_problem":
   outcome = fetch_problem(item, settings, deps)
   elif row.stage == "fetch_images":
   outcome = fetch_images(item, settings, deps)
   elif ...
   Instead, the engine does a single, clean lookup:
   handler = handler_for(row.stage)
   outcome = handler(item, settings, deps)

3. Enum validation:
   Notice HANDLERS[Stage(stage)]. If an invalid stage name somehow made its way in, Stage(stage) immediately raises a ValueError.
   ──────
   This completes Section 5 of our roadmap (Stage Handlers & Registry)!

Take a quick look. If it makes total sense, we'll move into Section 6: Task Creation and Validation (starting with eligibility.py).


 yes it was easy lets move onto next one

● Explored 1 file (eligibility.py) (ctrl+o to expand)

▸ Thought for 7s

  Now we step into Section 6: Task Creation and Validation.
  When a user in the UI says "Create a task for these 20 problems", we shouldn't blindly create a task for every one of them. What if some
  aren't solved yet? What if some are LeetCode Premium problems without content? What if a problem is already actively running inside
  another task?

  ### Step 11: Open eligibility.py

  Open eligibility.py side by side.
  This 60-line file acts as the bouncer: it inspects a list of problems and separates the ones that are ready to run from the ones that
  must be rejected.
  ──────
  #### 1. Preventing Double-Processing: busy_problem_ids(...)
    BUSY_ITEM_STATUSES = (
        TaskItem.Status.QUEUED,
        TaskItem.Status.RUNNING,
        TaskItem.Status.NEEDS_INPUT,
    )
    OPEN_TASK_STATUSES = (Task.Status.ACTIVE, Task.Status.PAUSED)


    def busy_problem_ids(problems: Sequence[Problem]) -> set[int]:
        """Problems that are unfinished in another open task."""
        return set(
            TaskItem.objects.filter(
                problem__in=list(problems),
                status__in=BUSY_ITEM_STATUSES,
                task__status__in=OPEN_TASK_STATUSES,
            ).values_list("problem_id", flat=True)
        )
  If Problem #1 is already running or waiting for review in Task A, we cannot let Task B start running it at the same time!
  This function does a single, fast SQL query (values_list("problem_id", flat=True)) to find any problems in the request that are
  currently "busy" in an open task.
  ──────
  #### 2. The Rejection Rules: rejection_reason(...)
  For any given problem, this function checks why it cannot join:
    def rejection_reason(problem: Problem, busy_ids: set[int]) -> str | None:
        """Why this problem can't join a task, or None if it can."""
        if not problem.solved:
            return "not solved yet"
        if problem.leetcode_id is None:
            return "not fully discovered yet (no LeetCode id)"
        if problem.paid_only and not problem.content_remote_markdown:
            return "premium problem: its statement isn't available"
        if problem.pk in busy_ids:
            return "already in a running task"
        return None

  • not problem.solved: The entire point of LeetNotes is writing study notes for your accepted submissions. If you haven't solved it on
  LeetCode yet, there's no code to explain.
  • problem.leetcode_id is None: The problem was discovered by title but hasn't had its metadata synced.
  • problem.paid_only and not problem.content_remote_markdown: Premium LeetCode problem where the statement is paywalled and not cached.
  • problem.pk in busy_ids: Avoid race conditions by ensuring only one active task touches a problem at a time.
  ──────
  #### 3. split_eligible(problems)

    def split_eligible(
        problems: Sequence[Problem],
    ) -> tuple[list[Problem], list[Rejection]]:
        busy_ids = busy_problem_ids(problems)
        eligible: list[Problem] = []
        rejected: list[Rejection] = []
        for problem in problems:
            reason = rejection_reason(problem, busy_ids)
            if reason:
                rejected.append(Rejection(problem, reason))
            else:
                eligible.append(problem)
        return eligible, rejected
  It takes a list of candidate problems, runs the checks, and returns:

  • eligible: A list of valid Problem objects, preserving the original ordering.
  • rejected: A list of Rejection dataclass instances (problem, reason), so the UI can clearly tell the user: "18 problems queued, 2
  problems skipped: Two Sum (already in a running task), Paint House (premium problem)".
  ──────
  Take a look at eligibility.py. Straightforward, right?

  Any questions on the busy checks or rejection criteria before we see how creation.py creates the task and inserts all the initial
  database rows?
────────────────────────────────────────────────────────────
> its straightforward lets move onto next

● Explored 1 file (creation.py) (ctrl+o to expand)

  Now we see how all our previous components come together to assemble a task from scratch!

  ### Step 12: Open creation.py
  Open creation.py side by side.
  This is the factory: whenever a user submits problems to be processed (via the web UI or API), create_task in this file is called.

  Look at lines 91–122 to see the main transaction:
    @transaction.atomic
    def create_task(
        problems: Iterable[Problem],
        overrides: dict | None = None,
        *,
        name: str = "",
    ) -> TaskCreation:

  Because it is decorated with @transaction.atomic, either the entire task and all its items/stages are successfully written to the
  database, or if anything fails, the database rolls back completely—leaving no half-created tasks or orphaned rows.
  ──────
  #### The Step-by-Step Creation Flow
  Let's trace what happens when create_task runs:
  1. Resolve and validate settings (lines 103–104):
    settings = settings_with_overrides(overrides)
    check_output_folders(settings)

      • Merges user global defaults with any overrides (like selecting a different AI model or toggling review).
      • Verifies that at least one output directory is configured so we don't do work that can't be saved.
  2. Deduplicate and filter eligible problems (lines 105–107):
    eligible, rejected = split_eligible(unique_problems(problems))
    if not eligible:
        raise NothingToDo(describe_rejections(rejected) or "No problems were chosen.")
      • unique_problems: Uses a dict comprehension {problem.pk: problem} to remove duplicates while preserving insertion order.
      • Runs split_eligible (which we saw in eligibility.py).
      • If zero problems can join, it raises NothingToDo with a detailed explanation of why every single problem was rejected.
  3. Plan stages for each problem (lines 109):
    plans = plan_problems(eligible, settings)
  For each problem, it calls initial_stages(problem, settings) (which we saw in plan.py). Remember, this checks hashes and cached sync
  tasks to mark stages as PENDING vs SKIPPED.
  4. Create the Task record (lines 110–113):
    task = Task.objects.create(
        name=name or default_task_name(len(plans)),
        settings=settings.model_dump(),
    )

      • If no custom name was given, it generates a nice default: e.g. "5 problems · Nov 03, 15:40".
      • It takes that JSON snapshot of settings.model_dump() so this task is frozen.
  5. Create the TaskItem and ItemStage rows (lines 114–121):
    for position, plan in enumerate(plans):
        item = TaskItem.objects.create(
            task=task,
            problem=plan.problem,
            position=position,
            stage=first_open_stage(plan),
        )
        ItemStage.objects.bulk_create(make_stage_rows(item, plan))
      • first_open_stage(plan): Scans STAGE_ORDER for the first stage that is not skipped. If stages 1–3 were already fetched, item.stage
      starts right at generate_prefill!
      • make_stage_rows(item, plan): Prepares all 6 ItemStage rows with their initial status (PENDING or SKIPPED) and detail reason.
      • bulk_create: Inserts all 6 rows in a single SQL INSERT statement for high performance.
  6. Return TaskCreation(task, rejected):
  Returns the newly created Task along with any rejected items so the caller knows who joined and who was excluded.
  ──────
  Notice how every single concept we've studied so far—definitions.py, models.py, settings.py, plan.py, and eligibility.py—just snapped
  together seamlessly like Lego bricks!

  Take a look through creation.py. Any questions on how create_task, first_open_stage, or make_stage_rows work before we check the
  companion preview/summary files?

────────────────────────────────────────────────────────────
> it was smooth understood everything! lets goo to the next

● Explored 2 files (preview.py, summary.py) (ctrl+o to expand)

▸ Thought for 9s
  Next up is the "dry-run" capability of our task system:
  ### Step 13: Open preview.py
  Open preview.py side by side.
  Before a user clicks "Start Task" in the web interface, we don't want them to be surprised by what's about to happen—especially when
  external API calls (costing money/tokens) or local file modifications are involved.

  This file provides a zero-side-effect simulation of what a task would do if created right now.
  ──────
  #### 1. What a Preview Reports: PlanPreview

    @dataclass(frozen=True)
    class PlanPreview:
        eligible: int
        rejected: list[Rejection]
        stage_runs: dict[Stage, int]  # how many problems will actually run each stage
        needs_fetching: int           # problems with at least one fetch stage to run
        edited_notes: int             # existing notes you edited, which will be kept
        warnings: list[str] = field(default_factory=list)
  Look at the granularity here:
  • eligible & rejected: How many problems qualified vs were disqualified.
  • stage_runs: A dictionary mapping each Stage enum to how many problems actually have work to do (meaning they start as PENDING, not
  SKIPPED).
  • needs_fetching: How many problems need network calls to LeetCode.
  • edited_notes: How many notes on disk were manually modified by the user and will be safely preserved.
  • warnings: Configuration issues (like forgetting to set an output folder).
  ──────
  #### 2. Inspecting Disk and Edited Notes: count_edited_notes(...)
    def count_edited_notes(problems: Iterable[Problem], output_dirs: list[str]) -> int:
        """Notes already in the output folders that you edited (or that predate tracking)."""
        return sum(
            inspect_note(problem, kind, folder).state in EDITED_STATES
            for problem in problems
            for kind in NoteRecord.Kind.values
            for folder in output_dirs
        )
  Before generating notes, it reaches out to the notes app via inspect_note(...) to check the actual filesystem. If you wrote personal
  comments or custom solutions in your local markdown files, LeetNotes detects NoteState.MODIFIED and tells the preview: "We found X
  edited notes; they will be kept safe."
  ──────
  #### 3. Building the Preview: preview_plan(...)

  Notice how cleanly it uses the same functions we saw in eligibility.py and creation.py:

    def preview_plan(
        problems: Iterable[Problem], overrides: dict | None = None
    ) -> PlanPreview:
        settings = settings_with_overrides(overrides)
        eligible, rejected = split_eligible(unique_problems(problems))
        plans = plan_problems(eligible, settings)
        kept = (
            0
            if settings.replace_edited
            else count_edited_notes(eligible, settings.output_dirs)
        )
        return PlanPreview(...)

  Because it calls plan_problems(eligible, settings) without calling Task.objects.create(...), it calculates the exact outcome in memory
  without touching the database!
  ──────
  #### 4. Human-Readable Summary: describe_preview(...)

  It formats everything into a clean one-liner for UI tooltips or CLI output:
  │ "5 problems: 2 to fetch, 3 AI notes to generate, 1 edited note will be kept."
  ──────
  Take a look through preview.py. Does the dry-run concept and how it reuses plan_problems make sense?
  Whenever you're ready, let me know and we'll check out its tiny sibling summary.py!

────────────────────────────────────────────────────────────
> yes it was clear to me i think we can move onto next
▸ Thought for 8s

  Now for the companion reporting utility:
  ### Step 14: Open summary.py
  Open summary.py in your editor.

  This is a concise, 31-line module used to print quick status summaries of recent tasks—useful for management commands, CLI tools, and
  logging.

    """Short plain-text summaries of tasks, for the command line."""

    from collections import Counter

    from pipeline.models import Task, TaskItem


    def recent_tasks(limit: int = 5) -> list[Task]:
        return list(Task.objects.order_by("-created_at")[:limit])


    def item_counts(task: Task) -> Counter:
        return Counter(task.items.values_list("status", flat=True))


    def describe_counts(counts: Counter) -> str:
        parts = [
            f"{counts[status]} {status.value.replace('_', ' ')}"
            for status in TaskItem.Status
            if counts[status]
        ]
        return ", ".join(parts) or "no items"


    def describe_task(task: Task) -> str:
        line = f"{task.name} [{task.status}]: {describe_counts(item_counts(task))}"
        return f"{line} ({task.pause_reason})" if task.pause_reason else line


    def describe_recent_tasks(limit: int = 5) -> list[str]:
        return [describe_task(task) for task in recent_tasks(limit)]
  ──────
  #### How it works:

  1. **item_counts(task)**:
  Extracts all item statuses with a single column query values_list("status", flat=True) and counts them using Python's standard
  collections.Counter.
  2. **describe_counts(counts)**:
  Iterates through TaskItem.Status in priority order and builds a readable string like:
  "3 completed, 1 needs input, 1 queued".
  3. **describe_task(task)**:
  Assembles the full line:
  "Task #12 [paused]: 3 completed, 1 queued (LeetCode rate limit reached)".
  ──────
  That wraps up Section 6! Now we are ready to climb into the core engine and runtime:
  • engine.py
  • locking.py
  • pacing.py
  • recovery.py
  • worker.py

  Ready to tackle engine.py? Let me know!
────────────────────────────────────────────────────────────
> okay ready for the engine.py!

● Explored 1 file (engine.py) (ctrl+o to expand)

▸ Thought for 10s
  Welcome to the beating heart of the pipeline! 🚀

  ### Step 15: Open engine.py

  Open engine.py side by side.
  Take a look at line 1's docstring:

  │ "The engine: runs one stage of one item at a time and records what happened."
  Notice what this is doing: it executes one atomic stage at a time, checks the outcome, updates the database via our transitions, and
  decides what to do next.

  Let's dissect this file section by section:
  ──────
  #### 1. Deciding What to Run Next (lines 57–83)
  How does the engine pick the next slice of work?
    def queued_items() -> QuerySet[TaskItem]:
        """Items an active task could run right now."""
        return TaskItem.objects.filter(
            status=TaskItem.Status.QUEUED, task__status=Task.Status.ACTIVE
        )


    def next_queued_item(task: Task | None = None) -> TaskItem | None:
        items = queued_items()
        if task is not None:
            items = items.filter(task=task)
        return (
            items.select_related("task", "problem")
            .order_by("task__created_at", "task_id", "position")
            .first()
        )


    def next_pending_stage(item: TaskItem) -> ItemStage | None:
        """The first stage of this item that hasn't run yet."""
        return item.stages.filter(status=ItemStage.Status.PENDING).order_by("order").first()
  1. next_queued_item: Fair FIFO scheduling. It orders by task__created_at, so older tasks get priority, and then by position (the order
  the user specified).
  2. next_pending_stage: Inside that problem, it finds the earliest stage whose status is still PENDING.
  ──────
  #### 2. Running a Single Stage: run_stage(...)
  This is where all the error hierarchies and transitions we studied earlier come into action:
    def run_stage(item: TaskItem, row: ItemStage, deps: StageDeps) -> None:
        begin_stage(item, row)
        try:
            outcome = handler_for(row.stage)(item, task_settings(item.task), deps)
        except PauseTask as exc:
            pause_for_error(item, row, str(exc))
        except ACCOUNT_ERRORS as exc:
            pause_for_error(item, row, result_for_error(exc).message)
        except (StageError, LeetCodeError) as exc:
            fail_stage(item, row, str(exc))
        except Exception as exc:  # a bug must fail this item, not stop the whole engine
            logger.exception("stage_crashed")
            fail_stage(item, row, f"Unexpected error: {type(exc).__name__}: {exc}")
        else:
            apply_outcome(item, row, outcome)
            advance_item(item, outcome)
        refresh_task_status(item.task)

  Look at how cleanly it handles every possible scenario:
  • Start: Calls begin_stage(item, row) ➡️ database marks it RUNNING.
  • Execution: Calls handler_for(row.stage) from registry.py.
  • Global Account Errors (PauseTask, ACCOUNT_ERRORS):
  If cookies expired (AuthError), or rate limits were triggered (RateLimitError), it calls pause_for_error. This puts the current stage
  back to PENDING and sets task.status = PAUSED.
  • Problem Errors (StageError, LeetCodeError):
  If this specific problem had an issue (e.g. no accepted submission), calls fail_stage. Only this problem fails; the rest of the batch
  continues!
  • Unexpected Bugs (Exception):
  If there's an unhandled bug in the code, it logs the stack trace and fails only this item rather than crashing the background worker
  daemon!
  • Success (else block):
      • apply_outcome(item, row, outcome): marks stage DONE, SKIPPED, or WAITING.
      • advance_item(item, outcome): if there's another stage, calls requeue_item; otherwise complete_item.
  • Cleanup: Calls refresh_task_status to mark the task FINISHED if all items are done.
  ──────
  #### 3. Stepping with Pacing: step(...)
    BeforeStage = Callable[[TaskItem, ItemStage], bool]

    def step(
        deps: StageDeps | None = None,
        task: Task | None = None,
        before_stage: BeforeStage | None = None,
    ) -> StepResult | None:
  Notice the before_stage hook.
  Before executing run_stage, it runs:
    if before_stage and not (before_stage(item, row) and still_runnable(item)):
        return None

  • This hook is where rate limiting / pacing sleeps occur! If LeetCode requests need a 2-second gap, the hook sleeps here.
  • Notice still_runnable(item): If the hook slept for 2 seconds, and during those 2 seconds the user clicked "Pause" or "Cancel" in the
  browser, still_runnable detects that the status changed in the database and aborts before doing unnecessary work!
  • Look at line 143: with bound_contextvars(task=item.task_id, slug=item.problem.slug, stage=row.stage):
  Structured logging! Every log line emitted by any handler automatically includes the task ID, problem slug, and stage name.
  ──────
  #### 4. The Batch Loop: run_until_idle(...)

    def run_until_idle(
        deps: StageDeps | None = None,
        task: Task | None = None,
        max_steps: int = 10_000,
        before_stage: BeforeStage | None = None,
    ) -> list[StepResult]:
        results: list[StepResult] = []
        while (
            len(results) < max_steps
            and (result := step(deps, task, before_stage)) is not None
        ):
            results.append(result)
        return results
  This is used in management commands, background tasks, or unit tests to churn through steps until either the queue is empty or all items
  are parked waiting for input.
  ──────
  Take a good look at engine.py. How does it feel to see all the pieces we discussed (outcomes, transitions, registry, settings, deps)
  come together here?

  Do you have any questions on how run_stage or step works? When you're ready, we can check out the runtime helpers: locking.py and
  pacing.py!

────────────────────────────────────────────────────────────
> so the main function from here is the step function right? it also takes in a function argument and its called before it runs a stage, so
  that we can cleverly inject a pacer to give a break between stages, and it calls the run stage to actually run the stage
○ Thought for 6.4s (The core function indeed appears to be the "step" function, acting as an orchestrator for stages. It accepts a funct...)
● Explored 2 files (locking.py, pacing.py) (ctrl+o to expand)

▸ Thought for 6s
  Yes! Exactly! You nailed the architectural pattern! 🎯
  step(...) is indeed the core entry point of the engine.

  • It's a single unit of execution: "do one stage for one queued item".
  • By accepting before_stage: BeforeStage | None, it leaves the door wide open for middleware-style hooks without cluttering the engine
  with sleep timers, clocks, or shutdown signals.

  And your intuition about injecting a pacer is literally what the codebase does! Let's follow that exact trail right into:
  ──────
  ### Step 16: Open pacing.py

  Open pacing.py side by side.
  When communicating with LeetCode, hammering their servers with 50 rapid requests in 2 seconds will get your IP or account banned/rate-
  limited.
  This file introduces Pacer, which implements that exact before_stage hook!
  ──────
  #### 1. Only Pacing What Hits the Network

  Look at line 12:

    PACED_STAGES = {Stage.FETCH_PROBLEM, Stage.FETCH_IMAGES, Stage.FETCH_SUBMISSION}
  Stages like GENERATE_PREFILL, REVIEW, and SAVE_NOTES are either local CPU tasks or talk to your own AI API keys.
  PACED_STAGES explicitly targets the 3 stages that hit LeetCode directly.
  ──────
  #### 2. The Rate Calculation and Jitter
    JITTER_FRACTION = 0.2  # up to 20% extra, so the rhythm isn't perfectly regular


    def seconds_per_problem() -> float | None:
        pace = effective.problems_per_minute()  # e.g., 10 problems per minute
        return 60.0 / pace if pace else None  # e.g., 6.0 seconds per problem


    def random_jitter(interval: float) -> float:
        return random.uniform(0, interval * JITTER_FRACTION)

  If your configured pace is 10 problems/minute, the interval is 6 seconds.
  The random_jitter adds up to 20% random variance (e.g. 6.0 to 7.2 seconds). This ensures the traffic doesn't look like an automated
  machine pulsing with robotic precision.
  ──────
  #### 3. The Hook in Action: Pacer.before_stage(...)

        def before_stage(self, item: TaskItem, row: ItemStage) -> bool:
            """The engine's hook: returns False if the worker was stopped while waiting."""
            if row.stage not in PACED_STAGES or item.pk == self._last_item_id:
                return True
            return self.take_turn(item.pk)
  Look at this logic:

  1. If the stage is not a LeetCode stage (e.g. save_notes), return True immediately (zero delay).
  2. If this stage is for the same problem as the previous stage (e.g., FETCH_PROBLEM just finished, and now FETCH_IMAGES is running for
  that same problem), return True immediately.
  3. If it's a new problem starting its fetch, it calls take_turn(item.pk):
      • Computes remaining = last_start + gap + jitter - current_time.
      • If remaining > 0, it calls self.wait(remaining).
      • If the background worker was told to shutdown/pause while waiting, self.wait returns False, which tells step(...) to back out
      cleanly without executing!
  ──────
  Look at how cleanly Pacer.before_stage plugs right into engine.step(before_stage=pacer.before_stage).
  How does this pacer design feel?

  Whenever you're ready, we have two companion runtime files to look at before the worker daemon itself:
  • locking.py (preventing multiple worker processes from colliding)
  • recovery.py (handling crashes if the machine reboots or worker dies mid-stage)
────────────────────────────────────────────────────────────
> okay this feels good I think we can move on

● Explored 1 file (recovery.py) (ctrl+o to expand)

  Let's look at how we prevent two workers from clashing:
  ### Step 17: Open locking.py
  Open locking.py side by side.
  If a user opens two browser tabs, runs multiple CLI commands, or has multiple background processes running, what prevents two workers
  from processing the same queue at the same time and duplicating requests?
  This file implements an OS-level file lock:
    """A lock that only one holder (a process, or a thread of one) can have at a time."""

    import fcntl
    from pathlib import Path


    class FileLock:
        """An exclusive lock on a file. The operating system drops it if the process dies.

        Each FileLock opens the file itself, so two locks on the same path also keep each
        other out inside one process. (Linux and macOS only, like the rest of the app.)
        """

        def __init__(self, path: Path):
            self.path = path
            self._handle = None

        def acquire(self) -> bool:
            """Take the lock if nobody else holds it. Never waits."""
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            handle = open(self.path, "a+")
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                handle.close()
                return False
            self._handle = handle
            return True

        def release(self) -> None:
            if self._handle is not None:
                fcntl.flock(self._handle, fcntl.LOCK_UN)
                self._handle.close()
                self._handle = None
  ──────
  #### Why fcntl.flock with LOCK_EX | LOCK_NB?

  1. LOCK_EX (Exclusive Lock): Only one process or thread can hold this lock at any moment.
  2. LOCK_NB (Non-Blocking): Instead of freezing or waiting indefinitely if another worker is already running, acquire() immediately fails
  with BlockingIOError and returns False. The second worker can safely say "Another worker is already running, exiting."
  3. OS Cleanup on Crash: This is the most brilliant feature of kernel-level file locks. If someone pulls the power plug, runs kill -9, or
  the process crashes due to a Python segfault, the operating system kernel automatically drops the file lock. There is never a "stale PID
  lockfile" you have to manually delete after a crash!
  ──────
  How does this file lock look to you? Ready to see how we recover gracefully from a crash in recovery.py?

────────────────────────────────────────────────────────────
> okay I have no idea how this works like this is the first time I'm encountering a lock file being implemented in my application or ever in
  my life actually, first explain it to me what is a lock file, when is it used, why is it used, what problem does it solve, and finally how
  it comes into rescue in our specific in this case of our project
▸ Thought for 12s

  This is one of the most important concepts in backend engineering and operating systems! I'm really glad you asked, because once you
  understand lock files, you'll recognize them everywhere (Docker, Git, package managers, databases, and daemons).

  Let's break it down from scratch.
  ──────
  ### 1. The Real-World Metaphor: The Bathroom Key 🔑

  Imagine a busy coffee shop with a single single-stall restroom.
  • To prevent two people from walking in at the same time, the coffee shop has one physical brass key hanging on a peg by the counter.
  • If you want to use the restroom, you take the key.
  • If someone else walks up to the counter and sees the peg is empty, they know someone is inside. They don't try to force the door; they
  just wait or go back to their seat.
  • When you leave, you hang the key back on the peg.

  A Lock File is that physical brass key, but for computer programs.
  ──────
  ### 2. What Problem Does It Solve? (The Nightmare Scenario 💥)
  Our LeetNotes app has a background worker that fetches data, calls AI APIs, and writes Markdown notes to your disk.

  Suppose you start the worker:
    python manage.py run_worker

  Then, half an hour later, you forget it's running in another terminal window or background tab, and you start it again:

    python manage.py run_worker
  Now you have two worker processes running simultaneously on your machine. Here is what happens:

  1. Both query the database at the exact same millisecond:
      • Worker 1: "Give me the next queued item." ➡️ Database: "Problem #1 (Two Sum)"
      • Worker 2: "Give me the next queued item." ➡️ Database: "Problem #1 (Two Sum)"
  2. Double LeetCode Requests:
      • Both workers hit LeetCode's GraphQL endpoint for Two Sum at the same time. LeetCode detects suspicious concurrent scraping from
      your IP and bans or rate-limits your account.
  3. Double AI Cost:
      • Both workers build the prompt and send it to Claude / OpenAI. You just paid double the API cost for the exact same problem!
  4. File Corruption on Disk:
      • Both workers try to write Two Sum.md into your notes folder at the exact same second, interleaving chunks of text and corrupting
      the file!
  This disaster is called a Race Condition or Split-Brain execution.
  ──────
  ### 3. The Naive Way (And Why It Fails)

  A junior developer might try to solve this with a simple file check:
    # DON'T DO THIS! The buggy way:
    if os.path.exists("worker.lock"):
        print("Worker is already running!")
        exit(1)

    # Create the lock file
    open("worker.lock", "w").write("running")

    # Do work...
    run_worker()

    # Clean up
    os.remove("worker.lock")

  Why this breaks in the real world:
  What if your laptop battery dies while the worker is running? Or what if you press Ctrl+C or run kill -9?
  os.remove("worker.lock") never gets called.
  The next morning, you turn on your laptop and try to run the worker. It sees worker.lock still sitting on disk, says "Worker is already
  running!", and refuses to start! You have to manually find the file and delete it.
  ──────
  ### 4. The Professional Way: OS Kernel File Locks (fcntl.flock)

  This is what our FileLock does!

  Instead of relying on whether the file exists, the file data/worker.lock always exists.
  Instead, we ask the Linux / macOS Operating System Kernel to place an advisory lock on the file descriptor:

    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

  Look at the two flags:

  • fcntl.LOCK_EX (Exclusive): Tells the Linux kernel: "Only I am allowed to hold the key to this file right now."
  • fcntl.LOCK_NB (Non-Blocking): Tells the kernel: "If another process already has the key, don't freeze my terminal waiting. Immediately
  raise an error (BlockingIOError) so I can exit gracefully."
  #### The Kernel Superpower: Automatic Cleanup!
  Because the lock is tracked inside the operating system kernel's table of open file descriptors, if the worker process crashes, hits an
  exception, or gets killed (kill -9), the Linux kernel automatically closes the file and releases the lock instantly!

  No stale lock files, no manual cleanup needed.
  ──────
  ### 5. How It Rescues Our Project Specifically
  Take a look at your project root right now: there is a file called worker.lock!
  Whenever our worker starts up:
  1. It calls lock.acquire().
  2. If it succeeds, it proceeds knowing it is the only worker in the world touching the database and notes folder.
  3. If another worker (or a web request trying to spawn an inline worker) calls lock.acquire(), it immediately gets False:
  │ "Ah, another worker is already doing the job. I'll step aside."
  Does that explain what a lock file is and why it's so critical for preventing double-work and corrupted notes?

────────────────────────────────────────────────────────────
> okay I understood how the lock file works but I have a doubt in our case does it allow two tasks(two list of problems) running togehter?
  or it only runs one task at a time in the order of creation and completes them? and then moves on to the other? and what if the user
  pauses one task while its running and then starts the other task? will it work?

▸ Thought for 11s

  You are asking the exact right questions! This touches the heart of how our task scheduler works.

  The short answer is:
  1. The lock file is for the worker process (the execution thread), not the task. There is only one worker process running, but that
  single worker can manage multiple tasks in the database.
  2. Yes! Tasks run in order of creation (FIFO).
  3. Yes! If you pause Task 1, the worker immediately switches to Task 2.

  Let's look at the exact code in engine.py:57-77 to see why:
  ──────
  ### 1. How the Worker Chooses Work: The SQL Query

  Whenever the worker is ready to run a stage, it executes this query:

    def queued_items() -> QuerySet[TaskItem]:
        return TaskItem.objects.filter(
            status=TaskItem.Status.QUEUED,
            task__status=Task.Status.ACTIVE    # <--- NOTE THIS!
        )

    def next_queued_item(task: Task | None = None) -> TaskItem | None:
        return (
            items.select_related("task", "problem")
            .order_by("task__created_at", "task_id", "position")  # <--- AND THIS!
            .first()
        )

  Look at the two rules hidden in those lines:

  1. task__status=ACTIVE: It will only consider items belonging to an ACTIVE task.
  2. order_by("task__created_at", "task_id", "position"): It sorts by task creation date first!
  ──────
  ### 2. Scenario A: You create Task 1 (10 problems), then create Task 2 (5 problems)
  Both are ACTIVE.

  • The worker queries for the next item. Because Task 1 was created earlier (task__created_at), the worker picks Item #1 of Task 1.
  • Next step: Item #2 of Task 1.
  • It continues through Task 1 until all items in Task 1 are either finished or waiting for review.
  • Once Task 1 has no more QUEUED items, the database query automatically yields Item #1 of Task 2!

  So they don't interleave randomly; Task 1 finishes first, then Task 2 starts.
  ──────
  ### 3. Scenario B: You PAUSE Task 1 while it's running, and Task 2 is queued

  Here is the beauty of this architecture:
  1. Task 1 is running Problem #3.
  2. You click "Pause" on Task 1 in the web UI.
  3. Django updates Task 1 in the database: status = "paused".
  4. As soon as Problem #3's current stage finishes, the worker asks for the next item:
    TaskItem.objects.filter(status="queued", task__status="active")

  5. Since Task 1 is PAUSED, the database completely excludes Task 1 from the query!
  6. Who is the oldest ACTIVE task now? Task 2!
  7. The worker immediately and seamlessly starts processing Task 2!
  8. If tomorrow you click "Resume" on Task 1:
  Task 1 becomes ACTIVE again. Because Task 1 has an older created_at timestamp than Task 2, the worker will prioritize finishing Task 1
  again.
  ──────
  ### 4. What about problem conflicts?

  What if Task 1 has "Two Sum", and Task 2 also wanted "Two Sum"?
  Remember our check in eligibility.py:23-31:

    OPEN_TASK_STATUSES = (Task.Status.ACTIVE, Task.Status.PAUSED)

  When you create Task 2, split_eligible checks if any of the problems are currently in an ACTIVE or PAUSED task. If "Two Sum" is in Task
  1, Task 2 will reject "Two Sum" with: "already in a running task".

  So two tasks can never step on each other's toes!
  Does that make the relationship between the worker lock, task priorities, and pausing clear?

────────────────────────────────────────────────────────────
> okay and what will happen if lets say the problem 1 is waiting for the manual input from the user, it marked as waiting and moved to
  problem 2 and while its doing problem 2 the user goes ahead and give the manul input it needed in problem 1, and hits continue or whatever
  which problem's stage will it pick it up? problem 1's or continue doing the 2nd?
● Ran (git grep -n "manual_prefill" pipeline/)
● Explored 1 file (actions.py) (ctrl+o to expand)

▸ Thought for 18s

  What a brilliant question! This is the kind of question that shows you truly understand the moving parts.

  Here is the exact step-by-step timeline of what happens:
  ──────
  ### The Setup:
  • Problem 1 (Two Sum): Position 0. Hit a review check or manual input. Its status is NEEDS_INPUT (parked).
  • Problem 2 (Add Two Numbers): Position 1. Currently RUNNING Stage 1: FETCH_PROBLEM.
  ──────
  ### What happens in real-time:

  #### 1. In the Middle of Problem 2:
  The worker is right in the middle of executing fetch_problem for Problem 2. A stage execution is atomic—it won't violently abort mid-
  HTTP request. It will finish fetching the problem statement for Problem 2.

  #### 2. You Submit the Input for Problem 1 in the UI:
  While Problem 2 is fetching, you click "Submit / Approve" for Problem 1 in your browser.
  This triggers finish_waiting_stage(...):

  1. It marks Problem 1's review stage as DONE.
  2. It calls advance_item(item, outcome).
  3. advance_item sees Problem 1 has one pending stage left (SAVE_NOTES).
  4. It calls requeue_item(item, stage="save_notes").
  5. Problem 1 is now back in status QUEUED!

  #### 3. Problem 2 Finishes its Current Stage:

  Problem 2's FETCH_PROBLEM finishes.
  The engine calls requeue_item on Problem 2 so it can do its next stage (FETCH_IMAGES).
  Problem 2 is also now in status QUEUED.

  #### 4. The Deciding Moment: Who gets picked next?

  Now both problems are in status QUEUED:

  • Problem 1 is QUEUED (ready for SAVE_NOTES). Its position = 0.
  • Problem 2 is QUEUED (ready for FETCH_IMAGES). Its position = 1.
  The worker runs the selection query in engine.py:74-76:

    items.order_by("task__created_at", "task_id", "position").first()

  Look at the tie-breaker: "position"!
  Because Problem 1 has position = 0 and Problem 2 has position = 1:
  Problem 1 wins!

  The worker immediately picks up Problem 1 and runs its SAVE_NOTES stage. Once Problem 1 is completely finished, the worker turns right
  back to Problem 2 and continues with FETCH_IMAGES!
  ──────
  Everything stays orderly, predictable, and prioritized by the original problem order you gave it.

  Pretty neat, isn't it? Ready to check out recovery.py next?

────────────────────────────────────────────────────────────
> Damn that's cool! okay now I understood lets move on

▸ Thought for 11s

  It really is a wonderfully thought-out system!

  Now, let's look at another piece of defensive engineering: what happens when things crash unexpectedly?

  ### Step 18: Open recovery.py
  Open recovery.py side by side.
  Imagine this scenario:
  1. The worker is executing FETCH_IMAGES for "Two Sum".
  2. In the database, TaskItem.status = RUNNING and ItemStage.status = RUNNING.
  3. Suddenly, your laptop battery dies, or someone forcefully kills the terminal (kill -9).
  4. When you boot your laptop back up tomorrow, what happens?
  If we didn't have recovery:

  • The database would still say RUNNING.
  • But the worker query only looks for QUEUED items (status="queued").
  • "Two Sum" would be stuck in RUNNING forever as a zombie item!

  pipeline/runtime/recovery.py heals zombie items on startup.
  ──────
  #### 1. When is it safe to recover?
  Look at the docstring for recover_interrupted():

  │ "Call this only when no worker is running (the worker does, once it holds its lock): then nothing can really be running, so anything
  │ marked running is left over."
  This is where our FileLock from earlier pays off!
  Because the newly started worker has successfully acquired the lock, it has 100% mathematical certainty that no other worker is alive.
  Therefore, any row in the database marked RUNNING is guaranteed to be a dead leftover from a crash.
  ──────
  #### 2. The Two Healing Steps
  Recovery runs two targeted passes:
  ##### Step A: put_back_interrupted_stages()

    def put_back_interrupted_stages() -> int:
        rows = list(
            ItemStage.objects.filter(status=ItemStage.Status.RUNNING).select_related(
                "item", "item__task"
            )
        )
        for row in rows:
            put_back(row.item, row)
        return len(rows)

  • Finds any ItemStage row stuck in RUNNING.
  • Calls put_back(row.item, row).
  • Remember put_back: it sets row.status = PENDING and item.status = QUEUED.
  • This means the interrupted stage will simply be retried cleanly from the beginning!

  ##### Step B: settle_stray_items()

    def settle_stray_items() -> int:
        stray = list(
            TaskItem.objects.filter(status=TaskItem.Status.RUNNING).select_related("task")
        )
        for item in stray:
            settle_item(item)
        return len(stray)

  • What if the stage handler finished and marked ItemStage = DONE, but the crash happened one millisecond before TaskItem was requeued?
  • settle_item: Looks up next_pending_stage(item).
      • If there are more stages left: requeues the item for the next stage.
      • If all stages were done: marks the item COMPLETED and refreshes the task.
  ──────
  With those two quick queries on startup, the system is completely self-healing! No manual database surgery or corrupted tasks after an
  unexpected crash.
  How does that recovery logic feel?

  If you're ready, we can move right into worker.py, which brings the lock, recovery, pacer, and engine into the actual running background
  process!

────────────────────────────────────────────────────────────
> I understood but just check my understanding and correct me if im wrong, the put back interrupted stages, it just simply queris for the
  running ItemStages which is not possible as the worker just aquired the lock, so we put all of them back to pending and status queued, and
  settle stray items is for the point where the problem is still in running stage so we fetch the parent task object and set it to queued
  again or mark it done if no pending problems are left

▸ Thought for 25s

  You have understood almost 100% of it! That's impressive.

  Just one tiny clarification on the second part:

  ### 1. put_back_interrupted_stages: Exactly right! 🎯

  Since the worker just acquired the exclusive OS lock, no other worker exists. Any ItemStage marked RUNNING was cut off mid-execution by
  a crash. So we reset the ItemStage back to PENDING and its problem (TaskItem) back to QUEUED so it will be retried cleanly.
  ──────
  ### 2. settle_stray_items: The tiny nuance:

  In this case, it's checking the TaskItem (the problem record itself), not the parent Task.

  Imagine this exact millisecond:

  1. FETCH_IMAGES finished and saved ItemStage.status = "done".
  2. But right before the engine could call requeue_item(item, stage="generate_prefill"), the machine crashed.
  3. Now:
      • None of the ItemStage rows are RUNNING (so put_back_interrupted_stages didn't touch it).
      • But the TaskItem is still stuck with status = "running".
  4. settle_stray_items finds this stray TaskItem and looks at its stages:
      • "Does this problem still have stages left to do?"
          • Yes: It has GENERATE_PREFILL left. It requeues the item (status = "queued", stage = "generate_prefill").
          • No: All 6 stages were already done. It completes the item (status = "completed") and marks the parent Task finished if all
          other problems are also done.


  ──────
  Does that distinction between an interrupted ItemStage vs a stray TaskItem make sense?
  If so, let's look at the conductor of the runtime:
  ### Step 19: Open worker.py!

────────────────────────────────────────────────────────────
> yes understood let open worker.py now

● Explored 1 file (worker.py) (ctrl+o to expand)

▸ Thought for 9s
  Here is the crown jewel of the runtime layer!
  ### Step 19: Open worker.py
  Open worker.py side by side.
  This file brings together everything we've looked at:

  • FileLock
  • recover_interrupted
  • Pacer
  • step
  It has two core classes:
  1. **Worker**: The worker event loop.
  2. **WorkerManager**: The thread manager that runs inside the web server process.
  ──────
  #### 1. The Worker Lifecycle: run()
  Look at what happens when worker.run() is called:

    def run(self) -> bool:
        """Work until idle or stopped. Returns False if another worker already holds the lock."""
        if not self.lock.acquire():
            logger.info("worker_not_started", reason="another worker is running")
            return False
        try:
            self.recover_leftovers()
            self.work_until_idle()
        finally:
            self.lock.release()
        return True

  Trace this sequence:

  1. Acquire the lock: If another process is running, exit immediately.
  2. Recover leftovers: Now that it has the lock, call recover_interrupted() to fix any leftover rows from a previous crash.
  3. Run loop (work_until_idle): Execute work.
  4. Finally release the lock: Guarantees the lock is freed when the worker finishes.
  ──────
  #### 2. The Worker Event Loop: work_until_idle()
  Inside the loop, the worker determines its next_move():

    class Move(StrEnum):
        WORKED = "worked"  # a stage ran
        HELD = "held"      # work exists but wasn't run (e.g. pacer sleeping, or task paused)
        IDLE = "idle"      # no work in the database
    def next_move(self) -> Move:
        if step(self.deps, before_stage=self.pacer.before_stage) is not None:
            return Move.WORKED
        return Move.HELD if has_queued_work() else Move.IDLE

  Look at how cleanly it handles the three states:
  • If WORKED: A stage ran! Reset idle timer (idle_since = None).
  • If HELD: Work exists, but before_stage held it back (e.g. pacer is waiting between LeetCode requests). Sleep briefly
  (HELD_RETRY_SECONDS = 0.1) and check again.
  • If IDLE: No queued items exist in any active task.
      • Start measuring how long it has been idle.
      • Sleep for POLL_SECONDS = 2.0.
      • If it stays idle for more than DEFAULT_IDLE_TIMEOUT = 60.0 seconds: the worker gracefully shuts down and exits!
      • Why exit after 60 seconds? So background worker threads don't stay alive forever consuming memory when you're not using the app!
  ──────
  #### 3. Thread Management in Web Requests: WorkerManager

  When you are using the LeetNotes web dashboard, you don't want to open a separate terminal and remember to run a background command
  manually.
  You want the web app to automatically spin up a worker whenever you click "Create Task" or "Approve Review".

  That is what WorkerManager does:
    manager = WorkerManager()

    def ensure_worker_running() -> bool:
        return manager.ensure_running()
  When you click "Create Task" in the UI:

  1. The web view calls ensure_worker_running().
  2. WorkerManager checks its internal mutex:
      • If a worker thread is already running in the background of the web server, it returns False (let the existing thread keep running).
      • If no thread is running, it spawns a background daemon thread (spawn_daemon_thread).
  3. Notice lines 120–125:
    def run() -> None:
        try:
            target()
        finally:
            connections.close_all()
  In Django, background threads must explicitly close their database connections when terminating, otherwise you get leaked DB connections.
  spawn_daemon_thread takes care of this automatically!
  ──────
  Take a look through worker.py.

  How does it feel to see the lock, the pacer, recovery, the engine, and thread management all coordinating together? Any questions here
  before we look at the final orchestrator layer: control.py and actions.py?
────────────────────────────────────────────────────────────
> I understood the things you explained but look at the code there seems a lot to it like mutex, spawn, etc. and so many things you didn't
  explain I think it would be nice if you give me a easy to follow flowchart mermaid diagram explaining the worker and the workermanager

▸ Thought for 13s

  You're completely right—there is a lot of concurrency and multithreading happening under the hood here! Let's demystify it with a clear
  flowchart and break down what each piece (mutex, spawn, daemon, etc.) does.
  ──────
  ### The Complete Worker & WorkerManager Architecture

  Here is the exact flow from the moment you click a button in the web UI all the way through thread creation, locking, and execution:

  ┌────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │                             WorkerManager (Thread Controller inside Web Process)                               │
  │                                                                                                                │
  │                                                                                                                │
  │ ┌───────────────────────────────────────────┐   ┌────────────────────────────┐   ┌───────────────────────────┐ │
  │ │                                           │   │                            │   │                           │ │
  │ │ Web request calls ensure_worker_running() │   │ B -- "YES (thread exists)" │   │   B -- "NO (no thread)"   │ │
  │ │                                           │   │                            │   │                           │ │
  │ └─────────────────────┬─────────────────────┘   └──────────────┬─────────────┘   └─────────────┬─────────────┘ │
  │                       │                                        │                               │               │
  │                       ▼                                        ▼                               ▼               │
  │ ┌───────────────────────────────────────────┐   ┌────────────────────────────┐   ┌───────────────────────────┐ │
  │ │                                           │   │                            │   │                           │ │
  │ │           B{"Acquire _mutex lock          │   │                            │   │   Create Worker instance  │ │
            G -- "FAILED (another process holds it)" --> H["Exit immediately<br/>Release thread in WorkerManager"]
            G -- "SUCCESS (lock acquired)" --> I["recover_interrupted()<br/>Fix any leftover crash rows"]
            I --> J["work_until_idle() loop begins"]

            J --> K["Call next_move() -> step()"]

            K -- "Move.WORKED (ran a stage)" --> L["Reset idle_since = None<br/>Run next stage immediately"]
            L --> J

            K -- "Move.HELD (pacer waiting or task paused)" --> M["Sleep 0.1s (HELD_RETRY_SECONDS)"]
            M --> J

            K -- "Move.IDLE (no queued work)" --> N{"Idle for >= 60 seconds?"}
            N -- "NO" --> O["Sleep 2.0s (POLL_SECONDS)"]
            O --> J
            N -- "YES (timeout reached)" --> P["Break loop<br/>release FileLock"]
        end

        subgraph Cleanup["Thread Teardown & DB Cleanup"]
            P --> Q{"Under _mutex:<br/>Did new work arrive meanwhile?"}
            Q -- "YES" --> J
            Q -- "NO" --> R["Set self._worker = None"]
            R --> S["connections.close_all()<br/>Close Django DB connections<br/>Daemon Thread exits cleanly"]
        end
  ──────
  ### Key Concepts Demystified

  #### 1. What is the _mutex? (threading.Lock())

  • The Problem: What if you double-click the "Create Task" button, or two browser tabs hit the server at the exact same millisecond?
  • Both web requests would run ensure_worker_running(). Without protection, both would check if self._worker is None, both would see None,
  and both would spawn two separate threads in the same process!
  • The Solution: The _mutex is an in-memory thread lock. Only one request can enter the with self._mutex: block at a time. The first
  request sees _worker is None and sets self._worker = worker. The second request waits a microsecond, enters, sees _worker is not None,
  and safely exits.
  ──────
  #### 2. What is spawn_daemon_thread?

  In Python, threads come in two flavors:

  1. Normal Thread: Python will refuse to exit until this thread finishes. (If you pressed Ctrl+C in your terminal to stop Django, Django
  would freeze indefinitely waiting for the worker!).
  2. Daemon Thread (daemon=True): A background helper thread. If the main Django server process shuts down or is stopped, Python
  automatically terminates daemon threads immediately.

  Notice line 124 in worker.py:124:

    def run() -> None:
        try:
            target()
        finally:
            connections.close_all()

  Django maintains a pool of database connections per thread. If a background thread dies without calling connections.close_all(), those
  open SQLite / PostgreSQL handles remain orphaned in memory. The try ... finally guarantees that whenever the worker thread exits, all
  database connections are closed.
  ──────
  #### 3. Why Move.HELD?

  Look at line 108:

    def next_move(self) -> Move:
        if step(self.deps, before_stage=self.pacer.before_stage) is not None:
            return Move.WORKED
        return Move.HELD if has_queued_work() else Move.IDLE

  • Move.WORKED: A stage actually executed and moved an item forward.
  • Move.IDLE: There is literally zero queued work across all active tasks.
  • Move.HELD: There is queued work in the database, but step() returned None!
      • Why would that happen? Because the Pacer told the worker: "Wait! You just fetched Two Sum 2 seconds ago. LeetCode needs 4 more
      seconds of rest."
      • So the worker doesn't mark itself as "idle" (which would trigger the 60-second shutdown timer); instead it enters Move.HELD,
      sleeps for 0.1s, and tries again.

  ──────
  #### 4. The _work_until_nothing_is_left race prevention (lines 170–178)

  Look at this clever bit of engineering:
  What if the worker decides it has been idle for 60 seconds and is about to exit, but at that exact millisecond, you create a new task in
  your browser?

    with self._mutex:
        if not owned or worker.stop_event.is_set() or not has_queued_work():
            self._worker = None
            return

  Under the protection of the same _mutex, before the thread actually terminates, it checks has_queued_work(). If new work just snuck in,
  it keeps the thread alive and loops right back into worker.run()!
  ──────
  How does that flowchart and explanation feel? Does seeing the coordination between WorkerManager (managing threads) and Worker (managing
  stages and locks) make the picture clear?

────────────────────────────────────────────────────────────
> acutally we are in a terminal and the mermaid diagram for some reason only rendered halfway just give me the mermaid code I will copy
  paste it in the web live editor and see it

  Here is the clean Mermaid code block. You can copy and paste this straight into https://mermaid.live:

  ┌────────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
  │                             WorkerManager (Thread Controller inside Web Process)                               │
  │                                                                                                                │
  │                                                                                                                │
  │ ┌───────────────────────────────────────────┐   ┌────────────────────────────┐   ┌───────────────────────────┐ │
  │ │                                           │   │                            │   │                           │ │
  │ │ Web request calls ensure_worker_running() │   │ B -- "YES (thread exists)" │   │   B -- "NO (no thread)"   │ │
  │ │                                           │   │                            │   │                           │ │
  │ └─────────────────────┬─────────────────────┘   └──────────────┬─────────────┘   └─────────────┬─────────────┘ │
  │                       │                                        │                               │               │
  │                       ▼                                        ▼                               ▼               │
  │ ┌───────────────────────────────────────────┐   ┌────────────────────────────┐   ┌───────────────────────────┐ │
  │ │                                           │   │                            │   │                           │ │
│
  │ │                                           │   │                            │   │                           │ │ │                     │
│
  │ └─────────────────────┬─────────────────────┘   └──────────────┬─────────────┘   └─────────────┬─────────────┘ │ └─────────────────────┘
│
  │                       │                                        │                               │               │
│
  │                       ▼                                        ▼                               ▼               │
│
  │ ┌───────────────────────────────────────────┐   ┌────────────────────────────┐   ┌───────────────────────────┐ │
│
  │ │                                           │   │                            │   │                           │ │
│
  │ │           B{"Acquire _mutex lock          │   │                            │   │   Create Worker instance  │ │
│
  │ │                                           │   │  Do nothing, return False  │   │                           │ │
│
  │ │       Is _worker already running?"}       │   │                            │   │ Set self._worker = worker │ │
│
  │ │                                           │   │                            │   │                           │ │
│
  │ └───────────────────────────────────────────┘   └────────────────────────────┘   └─────────────┬─────────────┘ │
│
  │                                                                                                │               │
│
  │                                                                                                │               │
│
  │ ┌───────────────────────────────────────────┐                                                  │               │
│
  │ │                                           │                                                  │               │
│
  │ │               Release _mutex              │                                                  │               │
│
  │ │                                           │◄─────────────────────────────────────────────────┘               │
│
  │ │       Spawn background Daemon Thread      │                                                                  │
│
  │ │                                           │                                                                  │
│
  │ └─────────────────────┬─────────────────────┘                                                                  │
│
  │                       │                                                                                        │
│
  │                       ▼                                                                                        │
│
  │ ┌───────────────────────────────────────────┐                                                                  │
│

────────────────────────────────────────────────────────────
> no dont give it as code block it renders! right away give it in plain text

▸ Thought for 6s
  Here is the raw text (no code block) so your terminal won't try to render it:
  flowchart TD
  subgraph WM["WorkerManager (Thread Controller inside Web Process)"]
  A["Web request calls ensure_worker_running()"] --> B{"Acquire _mutex lock; Is _worker already running?"}
  B -- "YES (thread exists)" --> C["Do nothing, return False"]
  B -- "NO (no thread)" --> D["Create Worker instance; Set self._worker = worker"]
  D --> E["Release _mutex; Spawn background Daemon Thread"]
  E --> F["Daemon Thread starts running _run()"]
  end

    subgraph W["Worker (Background Thread Loop)"]
        F --> G["lock.acquire() on data/worker.lock"]
        G -- "FAILED (another process holds it)" --> H["Exit immediately; Release thread in WorkerManager"]
        G -- "SUCCESS (lock acquired)" --> I["recover_interrupted(); Fix any leftover crash rows"]
        I --> J["work_until_idle() loop begins"]

        J --> K["Call next_move() -> step()"]

        K -- "Move.WORKED (ran a stage)" --> L["Reset idle_since = None; Run next stage immediately"]
        L --> J

        K -- "Move.HELD (pacer waiting or task paused)" --> M["Sleep 0.1s (HELD_RETRY_SECONDS)"]
        M --> J

        K -- "Move.IDLE (no queued work)" --> N{"Idle for >= 60 seconds?"}
        N -- "NO" --> O["Sleep 2.0s (POLL_SECONDS)"]
        O --> J
        N -- "YES (timeout reached)" --> P["Break loop; release FileLock"]
    end

    subgraph Cleanup["Thread Teardown and DB Cleanup"]
        P --> Q{"Under _mutex: Did new work arrive meanwhile?"}
        Q -- "YES" --> J
        Q -- "NO" --> R["Set self._worker = None"]
        R --> S["connections.close_all(); Close Django DB connections; Daemon Thread exits cleanly"]
    end

────────────────────────────────────────────────────────────

> Okay I understood it what's next now?
○ Thought for 8.0s (Definitions, models, outcomes, and settings appear complete based on the roadmap. The handlers and registry require...)
● Explored 1 file (control.py) (ctrl+o to expand)
  We've reached the top of the mountain: Section 8: Top-Level Orchestration & Interfaces!

  Now we see the clean, public APIs that the rest of the app (like Django views, API endpoints, and CLI management commands) calls to
  drive everything.
  ### Step 20: Open control.py
  Open control.py in your editor.
  This file provides the top-level remote control for a task:
  • start_task
  • pause
  • resume
  • cancel

  Take a look at how beautifully simple it is (only 50 lines), because it delegates all the hard work to the modules we just studied:
    """What you can do to a task: start it, pause it, resume it, cancel it."""

    from collections.abc import Callable, Iterable

    from pipeline.models import Task
    from pipeline.runtime.worker import ensure_worker_running
    from problems.models import Problem

    from .creation import TaskCreation, create_task
    from .transitions import cancel_task, pause_task, resume_task

    WakeWorker = Callable[[], bool]


    def get_task(task_id: int) -> Task:
        return Task.objects.get(pk=task_id)


    def start_task(
        problems: Iterable[Problem],
        overrides: dict | None = None,
        *,
        name: str = "",
        wake: WakeWorker = ensure_worker_running,
    ) -> TaskCreation:
        """Create a task and make sure a worker is running to carry it out."""
        creation = create_task(problems, overrides, name=name)
        wake()
        return creation


    def pause(task_id: int) -> Task:
        """Stop picking up this task's items. A stage already running finishes first."""
        task = get_task(task_id)
        pause_task(task)
        return task


    def resume(task_id: int, wake: WakeWorker = ensure_worker_running) -> Task:
        task = get_task(task_id)
        resume_task(task)
        wake()
        return task


    def cancel(task_id: int) -> Task:
        """Stop the task for good. Its problems become available for other tasks."""
        task = get_task(task_id)
        cancel_task(task)
        return task
  ──────
  #### Notice the Design:

  1. The wake: WakeWorker = ensure_worker_running parameter:
      • When start_task creates the task and inserts all rows, it immediately calls wake() (ensure_worker_running()). This guarantees that
      a background worker thread is up and processing right away without waiting!
      • Same for resume: when you resume a paused task, it calls wake() in case the previous worker thread had timed out and exited while
      the task was paused.
      • In unit tests, you can pass wake=lambda: True to test task creation in isolation without accidentally kicking off a real
      background worker.
  2. Decoupled Actions:
      • pause simply updates the database row status to PAUSED. As we saw in engine.py, the worker notices this immediately on its next
      step.
      • cancel marks the task CANCELLED. As we saw in eligibility.py, because it's no longer in (ACTIVE, PAUSED), its problems immediately
      become free to join future tasks.
  ──────
  Take a look at control.py. How does this clean control layer feel?
  Next up, we will look at actions.py to see how user inputs (approving notes and pasting manual replies) are handled!

────────────────────────────────────────────────────────────
> understood lets move on

● Explored 1 file (actions.py) (ctrl+o to expand)

▸ Thought for 8s
  Now let's look at the companion to control.py: how you interact with individual items that are paused or need attention.

  ### Step 21: Open actions.py

  Open actions.py in your editor.
  While control.py controls the entire task (pause, resume, cancel), actions.py controls individual problem items that require human
  intervention:
  1. Approving a Review (approve_review, approve_all_reviews)
  2. Submitting a Manual AI Reply (submit_manual_reply)
  3. Rejecting a Review & Regenerating (reject_review)
  4. Retrying a Failed Problem (retry_failed_item)
  Let's look at each action:
  ──────
  #### 1. Approving Reviews (lines 66–96)

  Remember earlier when an item hit the REVIEW stage, it parked itself in status = NEEDS_INPUT and input_kind = "review".
  When you view the draft note in the UI and click "Approve":

    def approve_review(item_id: int, wake: WakeWorker = ensure_worker_running) -> TaskItem:
        item = get_item(item_id)
        approve_without_waking(item)
        wake()
        return item
  Look at what finish_waiting_stage does:
  • Sets ItemStage.status = DONE (detail: "approved").
  • Clears item.input_kind = "".
  • Calls advance_item(item, outcome) ➡️ requeues the item for its final stage: SAVE_NOTES!
  • Calls wake() to ensure the worker starts running immediately.
  • There is also approve_all_reviews(task_id) if you want to batch-approve every waiting problem in one click.
  ──────
  #### 2. Submitting a Pasted Reply: submit_manual_reply(...)

  What if you don't have an automated API key, or the LLM failed and triggered manual_fallback? The item is parked with input_kind =
  "manual_prefill".

  You copy the prompt, paste it into ChatGPT / Claude web chat, copy the response back, and paste it into the LeetNotes web UI:

    def submit_manual_reply(item_id, text, label="", wake=ensure_worker_running):
        item = get_item(item_id)
        row = waiting_stage(item, TaskItem.InputKind.MANUAL_PREFILL)
        check_reply_is_valid(text)
        save_pasted_reply(item, row, text, label)
        wake()
        return item
  • Validation (line 133): Checks if the pasted text can be parsed into a valid note format before saving. If you accidentally pasted
  random junk, it rejects it immediately with a friendly error.
  • Save & Advance (line 134): Saves the manual response into prefill.models.AIResponse, marks the prefill stage DONE, advances the item,
  and wakes the worker!
  ──────
  #### 3. Rejecting a Review & Regenerating: reject_review(...)
  What if the AI generated an explanation that you don't like?

    def reject_review(item_id: int, wake: WakeWorker = ensure_worker_running) -> TaskItem:
        item = get_item(item_id)
        waiting_stage(item, TaskItem.InputKind.REVIEW)
        reset_prefill_and_review(item)
        queue_item_at(item, Stage.GENERATE_PREFILL)
        wake()
        return item

  • It resets both the GENERATE_PREFILL and REVIEW stage rows back to PENDING (started_at=None, finished_at=None).
  • It sets item.stage = Stage.GENERATE_PREFILL and item.status = QUEUED.
  • Wakes the worker to prompt the AI model again for a fresh generation!
  ──────
  #### 4. Retrying a Failed Problem: retry_failed_item(...)
  Suppose you processed 20 problems, and 1 problem failed because of a temporary Wi-Fi glitch while downloading images.
  The other 19 problems finished, and the task status became FINISHED.

  You don't want to create a whole new task just for that one failed problem!

    def retry_failed_item(item_id: int, wake: WakeWorker = ensure_worker_running) -> TaskItem:
        item = get_item(item_id)
        check_item_can_retry(item)
        row = failed_stage(item)
        reset_stage_row(row)
        queue_item_at(item, row.stage)
        reopen_task_if_finished(item.task)
        wake()
        return item

  Look at line 214: reopen_task_if_finished(item.task)!
  If the parent task was FINISHED, it reopens the task back to ACTIVE, resets that one failed stage row to PENDING, sets item.status =
  QUEUED, and wakes the worker!
  ──────
  Notice how cleanly and defensively every edge case is handled. Every action:
  1. Validates prerequisites (check_task_is_open, check_item_waits_for, check_reply_is_valid).
  2. Updates stage rows and item status.
  3. Automatically calls wake() (ensure_worker_running()).

  How does actions.py look to you? Ready to see the final file in the pipeline app: views.py?

────────────────────────────────────────────────────────────
> okay understood next

● Explored 1 file (views.py)
● Ran 3 commands (git grep -n "pipeline.actions" ui/ ; git grep -n "pipeline.tasks.control" ui/: failed · git grep -n "start_task" · git...)
… 2 more actions (1 read, 1 command) …
● Explored 1 file (views.py)
● Ran (find pipeline/management -type f) failed (ctrl+o to expand)

▸ Thought for 17s
  If you open views.py and admin.py, you will notice something interesting: they are empty placeholders!
    from django.shortcuts import render

    # Create your views here.
  ### Why are there no views in pipeline?
  This is an intentional, modern Django architectural decision:
  • The pipeline app is designed strictly as a headless Domain Engine & Service layer. It doesn't care whether requests come from HTML
  templates, HTMX, a REST API, or CLI management commands.
  • All web presentation, HTMX endpoints, and dashboard templates live in the ui app, which imports the clean public API we just studied:
      • pipeline.tasks.control (start_task, pause, resume, cancel)
      • pipeline.actions (approve_review, submit_manual_reply, reject_review, retry_failed_item)
      • pipeline.tasks.preview (preview_plan)
  ──────
  ### 🎉 Congratulations! You Have Mastered the Entire Pipeline App!
  Let's look back at the whole dependency graph we climbed from the ground up:
                      ┌─────────────────────────────────────────────────────────┐
                      │                 USER / WEB INTERFACE                    │
                      │                        (ui app)                         │
                      └────────────────────────────┬────────────────────────────┘
                                                   │
                                         Calls Orchestrator APIs
                                                   │
                 ┌─────────────────────────────────┴─────────────────────────────────┐
                 │                                                                   │
                 ▼                                                                   ▼
       [tasks/control.py]                                                     [actions.py]
       (start_task, pause, cancel)                               (approve_review, submit_manual_reply)
                 │                                                                   │
                 ▼                                                                   ▼
       [tasks/creation.py]                                                 [runtime/worker.py]
       (plan, validate, bulk insert)                                 (Worker, WorkerManager, thread)
                 │                                                                   │
                 ├───────────────┬───────────────┐                                   ▼
                 ▼               ▼               ▼                              [engine.py]
          [eligibility.py]   [plan.py]     [settings.py]                  (step, run_stage, next item)
                 │               │               │                                   │
                 └───────────────┼───────────────┘                   ┌───────────────┼───────────────┐
                                 │                                   ▼               ▼               ▼
                                 ▼                             [locking.py]     [pacing.py]    [recovery.py]
                        [transitions.py]                        (FileLock)        (Pacer)       (heal zombies)
                      (state machine saves)                          │               │
                                 │                                   └───────────────┼───────────────┘
                                 ▼                                                   │
                          [models.py]                                                ▼
                  (Task, TaskItem, ItemStage)                              [stages/registry.py]
                                 │                                           (handler_for)
                                 ▼                                                   │
                       [stages/definitions.py]                                ┌──────┴──────┐
                        (6 sequential stages)                                 ▼             ▼
                                                                         [fetch.py]     [notes.py]
                                                                         (LeetCode)     (AI & Disk)
  ──────
  ### Summary Checklist of What You Now Know:

  1. Definitions & Models: How 6 linear stages map to Task, TaskItem, and ItemStage.
  2. Contracts & Plan: How StageOutcome and input_hash skip unchanged steps to save AI tokens and time.
  3. Execution & Registry: How engine.py steps through the queue atomically without monolithic loops.
  4. Resilience & Locking: How OS-level FileLock, Pacer, and recovery.py prevent race conditions, avoid rate limits, and heal after
  unexpected crashes.
  5. Human-in-the-Loop: How WAITING stages park items cleanly until you approve them via actions.py.

  You now have a deep, senior-level understanding of how the core engine of LeetNotes works! How do you feel about the entire pipeline
  architecture now? Any final questions on any part of it?
