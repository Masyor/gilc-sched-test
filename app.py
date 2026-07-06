import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import re

st.set_page_config(page_title="Standby Scheduler", layout="wide")

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
        
        # Overlap condition to prevent scheduling conflicts
        if max(start_time_mins, slot_start) < min(end_time_mins, slot_end):
            busy_slots.append(s)
            
    return busy_slots

def parse_days(days_str):
    days_map = {
        'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3,
        'Fri': 4, 'Sat': 5, 'Sun': 6
    }
    return [days_map[d.strip()] for d in days_str.split('/') if d.strip() in days_map]

st.title("Gilc Standby Scheduler (CP-SAT)")

# Sidebar Settings Configuration Panel
st.sidebar.header("🛠️ Schedule Configurations")
max_staff_per_slot = st.sidebar.slider(
    "Maximum Staff per Time Slot", 
    min_value=1, 
    max_value=10, 
    value=3, 
    help="Limits the maximum number of standby teachers assigned to any single 30-minute block."
)

st.markdown("Paste your CSV data below. Format: `Days,Time,Teacher Name`")
csv_input = st.text_area("CSV Data", height=200, placeholder="Mon/Wed/Fri, 09:45-11:15, John Doe\nTue/Thu, 17:30-20:00, Jane Smith")

if st.button("Generate Schedule"):
    if not csv_input.strip():
        st.error("Please provide CSV data.")
        st.stop()
        
    # Dictionary to keep track of precise teaching minutes directly from strings
    precise_teaching_minutes = {}
    
    try:
        data = []
        for line in csv_input.strip().split('\n'):
            parts = line.split(',')
            if len(parts) >= 3:
                days = parts[0].strip()
                times = parts[1].strip()
                name = parts[2].strip()
                
                # if header
                if 'Day' in days and 'Time' in times:
                    continue
                
                start_str, end_str = times.split('-')
                
                # Calculate precise absolute minutes for exact metrics tracking
                sh, sm = map(int, start_str.strip().split(':'))
                eh, em = map(int, end_str.strip().split(':'))
                duration_mins = (eh * 60 + em) - (sh * 60 + sm)
                
                parsed_days_list = parse_days(days)
                slots = get_busy_slots(start_str, end_str)
                
                # Add exact minutes to tracking dictionary for every active day
                if name not in precise_teaching_minutes:
                    precise_teaching_minutes[name] = 0
                precise_teaching_minutes[name] += duration_mins * len(parsed_days_list)
                
                for day in parsed_days_list:
                    for s in slots:
                        data.append({
                            'Teacher': name,
                            'Day': day,
                            'Slot': s
                        })
                    
        df = pd.DataFrame(data)
    except Exception as e:
        st.error(f"Error parsing data: {e}")
        st.stop()

    if df.empty:
        st.error("No valid data found.")
        st.stop()

    teachers = df['Teacher'].unique().tolist()
    num_teachers = len(teachers)
    
    # MWF 7-20 (14 to 40), TTh 8-20 (16 to 40), Sat 8-15:30 (16 to 31)
    operating_hours = {
        0: (14, 40), 1: (16, 40), 2: (14, 40),
        3: (16, 40), 4: (14, 40), 5: (16, 31)
    }
    
    # Build teacher busy matrix
    busy = {t: {d: set() for d in range(6)} for t in teachers}
    for _, row in df.iterrows():
        t = row['Teacher']
        d = row['Day']
        s = row['Slot']
        if d > 5: continue
        busy[t][d].add(s)

    model = cp_model.CpModel()
    
    # standby[t, d, s]
    standby = {}
    for t in teachers:
        for d in range(6):
            start_op, end_op = operating_hours[d]
            for s in range(start_op, end_op):
                if s in busy[t][d]:
                    standby[(t, d, s)] = model.NewIntVar(0, 0, f"sb_{t}_{d}_{s}")
                else:
                    standby[(t, d, s)] = model.NewBoolVar(f"sb_{t}_{d}_{s}")
                    
    # 1. Standby demand boundary conditions per slot
    for d in range(6):
        start_op, end_op = operating_hours[d]
        for s in range(start_op, end_op):
            model.Add(sum(standby[(t, d, s)] for t in teachers) >= 1)
            model.Add(sum(standby[(t, d, s)] for t in teachers) <= max_staff_per_slot)
            
    # 3. 1-Hour Consecutive Free Time (11:00 to 14:00 -> slots 22 to 28)
    for t in teachers:
        for d in range(6):
            free_blocks = []
            for start_s in range(22, 27):
                b1_busy = start_s in busy[t][d]
                b2_busy = (start_s + 1) in busy[t][d]
                if b1_busy or b2_busy:
                    continue
                block_free = model.NewBoolVar(f"free_{t}_{d}_{start_s}")
                model.Add(standby[(t, d, start_s)] + standby[(t, d, start_s + 1)] == 0).OnlyEnforceIf(block_free)
                free_blocks.append(block_free)
            
            if not free_blocks:
                pass
            else:
                model.Add(sum(free_blocks) >= 1)
                
    # 4. Total Effort Balancing (Note: The solver optimization model continues using 
    # slot counts internally for constraints math to keep variables as linear integers)
    total_teaching_slots = {t: sum(len(busy[t][d]) for d in range(6)) for t in teachers}
    total_workload = {}
    for t in teachers:
        total_workload[t] = model.NewIntVar(0, 1000, f"workload_{t}")
        sb_sum = sum(standby[(t, d, s)] for d in range(6) for s in range(operating_hours[d][0], operating_hours[d][1]))
        model.Add(total_workload[t] == total_teaching_slots[t] + sb_sum)
        
    max_workload = model.NewIntVar(0, 1000, "max_workload")
    min_workload = model.NewIntVar(0, 1000, "min_workload")
    for t in teachers:
        model.Add(max_workload >= total_workload[t])
        model.Add(min_workload <= total_workload[t])
        
    # 5. Penalize fragmented shifts
    transitions = []
    for t in teachers:
        for d in range(6):
            start_op, end_op = operating_hours[d]
            for s in range(start_op, end_op - 1):
                w1 = 1 if s in busy[t][d] else standby[(t, d, s)]
                w2 = 1 if (s+1) in busy[t][d] else standby[(t, d, s+1)]
                
                if isinstance(w1, int) and isinstance(w2, int):
                    continue
                else:
                    trans = model.NewBoolVar(f"trans_{t}_{d}_{s}")
                    model.Add(trans >= w1 - w2)
                    model.Add(trans >= w2 - w1)
                    transitions.append(trans)
                    
    total_transitions = model.NewIntVar(0, 10000, "total_transitions")
    model.Add(total_transitions == sum(transitions))
    
    # Objective
    diff = model.NewIntVar(0, 1000, "diff")
    model.Add(diff == max_workload - min_workload)
    model.Minimize(diff * 100 + total_transitions)
    
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)
    
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        st.success(f"🎉 Schedule successfully generated! (Status: {solver.StatusName(status)})")
        
        # Parse linear records
        res_data = []
        for d in range(6):
            start_op, end_op = operating_hours[d]
            for s in range(start_op, end_op):
                assigned = [t for t in teachers if solver.Value(standby[(t, d, s)]) == 1]
                time_str = f"{s//2:02d}:{(s%2)*30:02d} - {(s+1)//2:02d}:{((s+1)%2)*30:02d}"
                day_str = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d]
                res_data.append({
                    "Day": day_str,
                    "Time": time_str,
                    "Standby": ", ".join(assigned)
                })
                
        res_df = pd.DataFrame(res_data)
        days_order = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

        # -------------------------------------------------------------
        # THE VISUAL WHOLE WEEK PLANNER MATRIX
        # -------------------------------------------------------------
        st.markdown("---")
        st.subheader("🗓️ Master Weekly Standby Planner Grid")
        st.info(f"Below is the complete weekly view organized by time slots. (Max limit applied: {max_staff_per_slot} teachers per slot)")

        planner_pivot = res_df.pivot(index='Time', columns='Day', values='Standby').fillna("")
        planner_pivot = planner_pivot.reindex(columns=days_order).fillna("")

        def format_planner_cells(val):
            if val.strip() == "" or val.strip() == "—":
                return 'background-color: #f8f9fa; color: #cbd5e1; text-align: center; font-style: italic; border: 1px solid #e2e8f0;'
            return 'background-color: #f0fdf4; color: #166534; font-size: 13px; font-weight: 500; text-align: left; vertical-align: top; border: 1px solid #bbf7d0; padding: 6px;'

        display_pivot = planner_pivot.copy()
        for col in display_pivot.columns:
            display_pivot[col] = display_pivot[col].apply(lambda x: "—" if not str(x).strip() else x)

        styled_planner = display_pivot.style.map(format_planner_cells)
        st.dataframe(styled_planner, width='stretch', height=750)

        # -------------------------------------------------------------
        # DATA METRICS BREAKDOWN & EXPORTS
        # -------------------------------------------------------------
        st.markdown("---")
        st.subheader("📊 Workload Fairness & Balance Breakdown")
        
        breakdown = []
        for t in teachers:
            # Retrieve exact parsed teaching hours
            teach_hrs = precise_teaching_minutes.get(t, 0) / 60.0
            # Calculate standby hours from active assignments
            sb_hrs = sum(solver.Value(standby[(t, d, s)]) for d in range(6) for s in range(operating_hours[d][0], operating_hours[d][1])) / 2.0
            
            breakdown.append({
                "Teacher": t,
                "Teaching (hrs)": round(teach_hrs, 2),
                "Standby (hrs)": round(sb_hrs, 1),
                "Total Workload (hrs)": round(teach_hrs + sb_hrs, 2)
            })
            
        b_df = pd.DataFrame(breakdown).sort_values(by="Teacher")
        st.dataframe(b_df, width='stretch')
        
        csv_file = res_df.to_csv(index=False)
        st.download_button(label="📥 Download Master Standby CSV File", data=csv_file, file_name="standby_schedule.csv", mime="text/csv")
        
    else:
        st.error("No feasible schedule matching your rules could be found. Since max staff constraints were reduced, there might be a time slot where no one else is available to fill it. Try increasing the Maximum Staff slider in the sidebar.")
