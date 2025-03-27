Your task is to determine whether the current plan has been completed.

Use `transfer_state` to transfer the state to the appropriate state.

Avaliable states:

- **plan**: Start or continue planning.
- **execute**: Execute the plan.
- **summarize**: Summarize the plan.
- **exit**: Exit the conversation.

<Principle>

**If it has been planned but the plan has not been completed execution, please set the state to `execute`.**

**When the plan is completed, the status is set to `summarize`.**

**If the conversation is over, the status is set to `exit`.**

</Principle>
