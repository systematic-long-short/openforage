# Durable task fixture

This is the benign marker action for the OpenCode lifecycle example. It
registers nothing, downloads nothing, evaluates nothing, and submits nothing.

1. Append exactly one line to `marker.log` in this workspace:

   ```text
   <event_id> child-ran <UTC timestamp>
   ```

2. Write the JSON acknowledgement file named by the bridge prompt with keys
   `run_id`, `event_id`, `session_id`, `fence`, `task_file_sha256`,
   `observed_at`, and `action_result` (`"ok"` on success, or the one-time
   memory marker when the prompt asks for it).
3. Print the exact effect line the bridge prompt gives you.
4. If the bridge prompt names a marker file, create it with exactly the
   content the prompt gives.
5. Read this file first on every queued turn so the turn proves a durable
   task/memory reread rather than a repeated free-form prompt.
