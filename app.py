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
        
        # Overlap condition:
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
st.markdown("Paste your CSV data below. Format: `Days,Time,Teacher Name`")

csv_input = st.text_area("CSV Data", height=200, placeholder="Mon/Wed/Fri, 09:45-11:15, John Doe\nTue/Thu, 17:30-20:00, Jane Smith")

if st.button("Generate Schedule"):
    if not csv_input.strip():
        st.error("Please provide CSV data.")
        st.stop()
        
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
                slots = get_busy_slots(start_str, end_str)
                
                for day in parse_days(days):
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
    # busy[t][d][s] = True if teacher t is teaching on day d at slot s
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
                # If teaching, cannot do standby
                if s in busy[t][d]:
                    standby[(t, d, s)] = model.NewIntVar(0, 0, f"sb_{t}_{d}_{s}")
                else:
                    standby[(t, d, s)] = model.NewBoolVar(f"sb_{t}_{d}_{s}")
                    
    # 1. At least 1 teacher on standby every slot
    for d in range(6):
        start_op, end_op = operating_hours[d]
        for s in range(start_op, end_op):
            model.Add(sum(standby[(t, d, s)] for t in teachers) >= 1)
            
    # 3. 1-Hour Consecutive Free Time (11:00 to 14:00 -> slots 22 to 28)
    # 2 consecutive 30-min slots completely free (no teach, no standby)
    for t in teachers:
        for d in range(6):
            free_blocks = []
            for start_s in range(22, 27): # starts at 22, 23, 24, 25, 26
                # a block is free if not busy and not on standby for start_s and start_s + 1
                b1_busy = start_s in busy[t][d]
                b2_busy = (start_s + 1) in busy[t][d]
                if b1_busy or b2_busy:
                    continue
                # block is free if both standbys are 0
                block_free = model.NewBoolVar(f"free_{t}_{d}_{start_s}")
                # block_free == 1 => standby1 == 0 and standby2 == 0
                model.Add(standby[(t, d, start_s)] + standby[(t, d, start_s + 1)] == 0).OnlyEnforceIf(block_free)
                free_blocks.append(block_free)
            
            if not free_blocks:
                # Impossible constraint! Teacher is teaching too much during 11-14
                pass
            else:
                model.Add(sum(free_blocks) >= 1)
                
    # 4. Total Effort Balancing
    total_teaching = {t: sum(len(busy[t][d]) for d in range(6)) for t in teachers}
    total_workload = {}
    for t in teachers:
        total_workload[t] = model.NewIntVar(0, 1000, f"workload_{t}")
        # Workload = teaching + standby
        sb_sum = sum(standby[(t, d, s)] for d in range(6) for s in range(operating_hours[d][0], operating_hours[d][1]))
        model.Add(total_workload[t] == total_teaching[t] + sb_sum)
        
    # Minimize max workload to balance
    max_workload = model.NewIntVar(0, 1000, "max_workload")
    min_workload = model.NewIntVar(0, 1000, "min_workload")
    for t in teachers:
        model.Add(max_workload >= total_workload[t])
        model.Add(min_workload <= total_workload[t])
        
    # Minimize (max_workload - min_workload)
    
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
                elif isinstance(w1, int):
                    trans = model.NewBoolVar(f"trans_{t}_{d}_{s}")
                    model.Add(trans >= w1 - w2)
                    model.Add(trans >= w2 - w1)
                    transitions.append(trans)
                elif isinstance(w2, int):
                    trans = model.NewBoolVar(f"trans_{t}_{d}_{s}")
                    model.Add(trans >= w1 - w2)
                    model.Add(trans >= w2 - w1)
                    transitions.append(trans)
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
    solver.parameters.max_time_in_seconds = 15.0
    status = solver.Solve(model)
    
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        st.success(f"🎉 Schedule successfully generated! (Status: {solver.StatusName(status)})")
        
        # Build clean dataframe of raw results
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
        
        # -------------------------------------------------------------
        # BEAUTIFUL PLANNER VIEW 1: INDIVIDUAL TEACHER LOOKUP
        # -------------------------------------------------------------
        st.markdown("---")
        st.subheader("👤 Individual Teacher Week Planner")
        st.info("Select a teacher's name to view their color-coded standby timetable.")
        
        sorted_teachers = sorted(teachers)
        selected_teacher = st.selectbox("Select Teacher:", sorted_teachers)
        
        if selected_teacher:
            # Generate matrix framework for a complete week view
            all_times = sorted(res_df['Time'].unique())
            days_order = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
            planner_matrix = pd.DataFrame("", index=all_times, columns=days_order)
            
            for _, row in res_df.iterrows():
                d_str = row['Day']
                t_str = row['Time']
                assigned_list = [name.strip() for name in row['Standby'].split(',') if name.strip()]
                
                if selected_teacher in assigned_list:
                    planner_matrix.at[t_str, d_str] = "🟢 STANDBY"
                else:
                    planner_matrix.at[t_str, d_str] = "⚪ Free / Class"
            
            # Apply styling rules to the planner dataframe matrix
            def style_cells(val):
                if "STANDBY" in val:
                    return 'background-color: #d4edda; color: #155724; font-weight: bold; text-align: center; border: 1px solid #c3e6cb;'
                return 'background-color: #ffffff; color: #adb5bd; text-align: center; border: 1px solid #e9ecef;'
                
            styled_matrix = planner_matrix.style.applymap(style_cells)
            st.dataframe(styled_matrix, use_container_width=True, height=550)

        # -------------------------------------------------------------
        # BEAUTIFUL PLANNER VIEW 2: MASTER COORDINATOR WEEK GRID
        # -------------------------------------------------------------
        st.markdown("---")
        st.subheader("🏛️ Master Library Week Planner Grid")
        st.caption("A wide perspective overview of the week planner layout.")
        
        # Pivot structural rows into day columns
        master_pivot = res_df.pivot(index='Time', columns='Day', values='Standby').fillna("")
        master_pivot = master_pivot.reindex(columns=days_order).fillna("")
        
        # Use an elegant HTML rendering framework to neatly organize lists of names vertically
        html_table = "<table style='width:100%; border-collapse: collapse; font-family: sans-serif; font-size: 13px;'>"
        html_table += "<tr style='background-color: #1E3A8A; color: white; text-align: center;'>"
        html_table += "<th style='padding: 12px; border: 1px solid #cbd5e1;'>Time Slot</th>"
        for day in days_order:
            html_table += f"<th style='padding: 12px; border: 1px solid #cbd5e1;'>{day}</th>"
        html_table += "</tr>"
        
        for time_slot, row in master_pivot.iterrows():
            html_table += f"<tr><td style='padding: 8px; font-weight: bold; background-color: #f1f5f9; border: 1px solid #cbd5e1; white-space: nowrap; text-align: center;'>{time_slot}</td>"
            for day in days_order:
                content = row[day]
                # Reformat csv space lists into structured stacked list items
                formatted_content = content.replace(", ", "<br>")
                
                if len(content) > 40:
                    # If multiple teachers are assigned, wrap in a scroll block to maintain layout consistency
                    cell_html = f"<div style='max-height: 75px; overflow-y: auto; padding: 2px; line-height: 1.4; font-size: 11px; color:#1e293b;'>{formatted_content}</div>"
                elif content:
                    cell_html = f"<div style='line-height: 1.4; color:#1e293b;'>{formatted_content}</div>"
                else:
                    cell_html = "<span style='color:#cbd5e1;'>—</span>"
                    
                html_table += f"<td style='padding: 8px; border: 1px solid #cbd5e1; vertical-align: top; background-color: #ffffff;'>{cell_html}</td>"
            html_table += "</tr>"
        html_table += "</table>"
        
        st.components.v1.html(html_table, height=750, scroller=True)

        # -------------------------------------------------------------
        # DATA METRICS BREAKDOWN & EXPORTS
        # -------------------------------------------------------------
        st.markdown("---")
        st.subheader("📊 Workload Fairness & Analytics Balance")
        breakdown = []
        for t in teachers:
            teach_hrs = total_teaching[t] / 2
            sb_hrs = sum(solver.Value(standby[(t, d, s)]) for d in range(6) for s in range(operating_hours[d][0], operating_hours[d][1])) / 2
            breakdown.append({
                "Teacher": t,
                "Teaching (hrs)": teach_hrs,
                "Standby (hrs)": sb_hrs,
                "Total Workload (hrs)": teach_hrs + sb_hrs
            })
        b_df = pd.DataFrame(breakdown)
        st.dataframe(b_df, use_container_width=True)
        
        csv_file = res_df.to_csv(index=False)
        st.download_button(label="📥 Download Master Standby CSV File", data=csv_file, file_name="standby_schedule.csv", mime="text/csv")
        
    else:
        st.error("No feasible schedule matching your rules could be found. Please check your data or constraint flexibility settings.")
