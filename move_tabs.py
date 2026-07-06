with open("app.py", "r") as f:
    lines = f.readlines()

# find index of "# ── Lovable-Inspired Three-Column Console"
# find index of "# ── Progressive-Disclosure Evidence"

insert_idx = -1
start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if "# ── Lovable-Inspired Three-Column Console" in line:
        insert_idx = i
    if "# ── Progressive-Disclosure Evidence" in line:
        start_idx = i
    if "refresh_col, policy_col = st.columns" in line:
        end_idx = i

if insert_idx != -1 and start_idx != -1 and end_idx != -1:
    tabs_lines = lines[start_idx:end_idx]
    
    # Remove from bottom
    del lines[start_idx:end_idx]
    
    # Need to update insert_idx since we removed lines?
    # No, insert_idx is BEFORE start_idx, so it doesn't change.
    
    # Insert at insert_idx
    lines = lines[:insert_idx] + tabs_lines + ["\n"] + lines[insert_idx:]
    
    with open("app.py", "w") as f:
        f.writelines(lines)
    print("Successfully moved the tabs.")
else:
    print(f"Failed to find indices: insert={insert_idx}, start={start_idx}, end={end_idx}")

