# VS Code replay

This case was manually verified with Retrace's VS Code replay workflow.

Current local workflow:

1. Open the prepared internal VS Code replay workspace for this case.

2. Set a breakpoint at:

   `reproduce_nbconvert_1731.py:22`

3. Run:

   `Retrace nbconvert #1731`

4. Expected stop:

   `choose_output_format`

5. Expected locals:

   `metadata`, `output`, `filter_`

6. Expand `metadata` and confirm that the widget-state mimetype object is
   present but lacks the nested `state` key.

Before public release, replace local/private paths with portable replay
instructions or link to a hosted recording after secrets scan.

GIF pending.
