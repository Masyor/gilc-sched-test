def get_busy_slots(start_str, end_str):
    sh, sm = map(int, start_str.strip().split(':'))
    eh, em = map(int, end_str.strip().split(':'))
    
    start_time_mins = sh * 60 + sm
    end_time_mins = eh * 60 + em
    
    busy_slots = []
    # A slot s is from (s*30) to (s*30 + 30)
    for s in range(0, 48):
        slot_start = s * 30
        slot_end = slot_start + 30
        
        # Overlap condition:
        if max(start_time_mins, slot_start) < min(end_time_mins, slot_end):
            busy_slots.append(s)
            
    return busy_slots

print("09:45-11:15:", get_busy_slots("09:45", "11:15"))
print("09:30-11:00:", get_busy_slots("09:30", "11:00"))
