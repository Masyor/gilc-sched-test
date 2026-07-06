import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import hashlib

st.set_page_config(page_title="Standby Scheduler", layout="wide")

def get_busy_slots(start_str, end_str):
    sh, sm = map(int, start_str.strip().split(':'))
    eh, em = map(int, end_str.strip().split(':'))
    
    start_time_mins = sh * 60 + sm
    end_time_mins = eh * 60 + em
    
    busy_slots = []
    for s in range(0, 48):
        slot_start = s * 30
        slot_end = slot_start + 30
        if max(start_time_mins, slot_start) < min(end_time_mins, slot_end):
            busy_slots.append(s)
            
    return busy_slots

def parse_days(days_str):
    days_map = {
        'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3,
        'Fri': 4, 'Sat': 5, 'Sun': 6
    }
    return [days_map[d.strip()] for d in days_str.split('/') if d.strip() in days_map]

def generate_pastel_color(name_string):
    """Generates a stable, sophisticated toned-down pastel color based on text content."""
    if not name_string or name_string.strip() in ["", "—"]:
        return "background-color: #f8f9fa; color: #cbd5e1; font-style: italic;"
    
    # Hash the text string to get a consistent unique color index
    hash_digest = hashlib.md5(name_string.encode('utf-8')).hexdigest()
    hue = int(hash_digest[:4], 16) % 360
    
    # Using low saturation (35-45%) and high lightness (90-95%) ensures professional toned-down pastels
    bg_color = f"hsl({hue}, 45%, 93%)"
    text_color = f"hsl({hue}, 60%, 20%)"
    border_color = f"hsl({hue}, 40%, 82%)"
    
    return f"background-color: {bg_color}; color: {text_color}; border: 1px solid {border_color}; font-weight: 500; font-size: 12px;"

st.title("Gilc Standby Scheduler (CP-SAT)")

# Sidebar Settings Configuration Panel
st.sidebar.header("🛠️ Schedule Configurations")
max_staff_per_slot = st.sidebar.slider(
    "Maximum Staff per Time Slot", 
    min_value=1, max_value=10, value=3, 
    help="Limits the maximum number of standby teachers assigned to any single 30-minute block."
)

min_standby_hours = st.sidebar.slider(
    "Minimum Standby Target (Hours)", 
    min_value=0.0, max_value=10.0, value=1.0, step=0.5,
    help="Enforces that every parsed staff member receives at least this amount of total standby hours."
)

st.markdown("Paste your CSV data below. Format: `Days,Time,Teacher Name`")
csv_input = st.text_area("CSV Data", height=200, placeholder="Mon/Wed/Fri, 09:45-11:15, John Doe\nTue/Thu, 17:30-20:00, Jane Smith")

if st.button("Generate Schedule"):
    if not csv_input.strip():
        st.error("Please provide CSV data.")
        st.stop()
        
    precise_teaching_minutes = {}
    try:
        data = []
        for line in csv_input.strip().split('\n'):
            parts = line.split(',')
            if len(parts) >= 3:
                days = parts[0].strip()
                times = parts[1].strip()
                name = parts[2].strip()
                
                if 'Day' in days and 'Time' in times:
                    continue
                
                start_str, end_str = times.split('-')
                sh, sm = map(int, start_str.strip().split(':'))
                eh, em = map(int, end_str.strip().split(':'))
                duration_mins = (eh * 60 + em) - (sh * 60 + sm)
                
                parsed_days_list = parse_days(days)
                slots = get_busy_slots(start_str, end_str)
                
                if name not in precise_teaching_minutes:
                    precise_teaching_minutes[name] = 0
                precise_teaching_minutes[name] += duration_mins * len(parsed_days_list)
                
                for day in parsed_days_list:
                    for s in slots:
                        data.append({'Teacher': name, 'Day': day, 'Slot': s})
                    
        df = pd.DataFrame(data)
    except Exception as e:
        st.error(f"Error parsing data: {e}")
        st.stop()

    if df.empty:
        st.error("No valid data found.")
        st.stop()

    teachers = df['Teacher'].unique().tolist()
    min_standby_slots = int(min_standby_hours * 2) # Translate hours into 30m slots

    operating_hours = {
        0: (14, 40), 1: (16, 40), 2: (14, 40),
        3: (16, 40), 4: (14, 40), 5: (16, 31)
    }
    
    busy = {t: {d: set() for d in range(6)} for t in teachers}
    for _, row in df.iterrows():
        t = row['Teacher']
        d = row['Day']
        s = row['Slot']
        if d > 5: continue
        busy[t][d].add(s)

    model = cp_model.CpModel()
    standby = {}
    
    for t in teachers:
        for d in range(6):
            start_op, end_op = operating_hours[d]
            for s in range(start_op, end_op):
                if s in busy[t][d]:
                    standby[(t, d, s)] = model.NewIntVar(0, 0, f"sb_{t}_{d}_{s}")
                else:
                    standby[(t, d, s)] = model.NewBoolVar(f"sb_{t}_{d}_{s}")
                    
    # Slot assignments ceilings and floors
    for d in range(6):
        start_op, end_op = operating_hours[d]
        for s in range(start_op, end_op):
            model.Add(sum(standby[(t, d, s)] for t in teachers) >= 1)
            model.Add(sum(standby[(t, d, s)] for t in teachers) <= max_staff_per_slot)
            
    # Enforce minimum allocation per person
    for t in teachers:
        sb_sum_expr = sum(standby[(t, d, s)] for d in range(6) for s in range(operating_hours[d][0], operating_hours[d][1]))
        model.Add(sb_sum_expr >= min_standby_slots)
            
    # 1-Hour Consecutive Free Time
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
            if free_blocks:
                model.Add(sum(free_blocks) >= 1)
                
    # Total Effort Balancing
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
        
    # Fragment penalties
    transitions = []
    for t in teachers:
        for d in range(6):
            start_op, end_op = operating_hours[d]
            for s in range(start_op, end_op - 1):
                w1 = 1 if s in busy[t][d] else standby[(t, d, s)]
                w2 = 1 if (s+1) in busy[t][d] else standby[(t, d, s+1)]
                if not (isinstance(w1, int) and isinstance(w2, int)):
                    trans = model.NewBoolVar(f"trans_{t}_{d}_{s}")
                    model.Add(trans >= w1 - w2)
                    model.Add(trans >= w2 - w1)
                    transitions.append(trans)
                    
    total_transitions = model.NewIntVar(0, 10000, "total_transitions")
    model.Add(total_transitions == sum(transitions))
    
    diff = model.NewIntVar(0, 1000, "diff")
    model.Add(diff == max_workload - min_workload)
    model.Minimize(diff * 100 + total_transitions)
    
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 20.0
    status = solver.Solve(model)
    
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        st.success(f"🎉 Schedule successfully generated! (Status: {solver.StatusName(status)})")
        
        res_data = []
        for d in range(6):
            start_op, end_op = operating_hours[d]
            for s in range(start_op, end_op):
                assigned = [t for t in teachers if solver.Value(standby[(t, d, s)]) == 1]
                time_str = f"{s//2:02d}:{(s%2)*30:02d} - {(s+1)//2:02d}:{((s+1)%2)*30:02d}"
                day_str = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d]
                res_data.append({"Day": day_str, "Time": time_str, "Standby": ", ".join(assigned)})
                
        res_df = pd.DataFrame(res_data)
        days_order = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

        # -------------------------------------------------------------
        # THE VISUAL WEEK PLANNER MATRIX
        # -------------------------------------------------------------
        st.markdown("---")
        st.subheader("🗓️ Master Weekly Standby Planner Grid")
        
        planner_pivot = res_df.pivot(index='Time', columns='Day', values='Standby').fillna("")
        planner_pivot = planner_pivot.reindex(columns=days_order).fillna("")

        display_pivot = planner_pivot.copy()
        for col in display_pivot.columns:
            display_pivot[col] = display_pivot[col].apply(lambda x: "—" if not str(x).strip() else x)

        # Apply custom HSL dynamic pastel tone generator
        styled_planner = display_pivot.style.map(generate_pastel_color)
        st.dataframe(styled_planner, width='stretch', height=650)

        # -------------------------------------------------------------
        # DOWNLOADS & PRINT EXPORT EXTRAS
        # -------------------------------------------------------------
        col1, col2 = st.columns(2)
        
        with col1:
            # 1. Export the Pretty Grid itself as a human-readable Matrix CSV
            pretty_csv = display_pivot.to_csv(index=True)
            st.download_button(
                label="📥 Download Pretty Grid Planner (CSV)", 
                data=pretty_csv, 
                file_name="weekly_matrix_planner.csv", 
                mime="text/csv"
            )
            
        with col2:
            # 2. Browser print engine utility trigger to save as PDF or Print A4 layout
            print_button_html = """
            <script>
            function printPlanner() {
                var printWindow = window.open('', '_blank');
                var gridHtml = window.parent.document.querySelector('[data-testid="stDataFrame"]').outerHTML;
                printWindow.document.write('<html><head><title>Print Standby Schedule</title>');
                printWindow.document.write('<style>body{font-family:sans-serif;padding:20px;} table{width:100%;border-collapse:collapse;} th,td{border:1px solid #cbd5e1;padding:8px;text-align:left;font-size:12px;}</style>');
                printWindow.document.write('</head><body><h2>Master Weekly Standby Planner Grid</h2>');
                printWindow.document.write(gridHtml);
                printWindow.document.write('<script>window.onload = function() { window.print(); window.close(); }</sc' + 'ript></body></html>');
                printWindow.document.close();
            }
            </script>
            <button onclick="printPlanner()" style="width:100%; padding: 0.5rem; background-color: #1E3A8A; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: bold;">
                🖨️ Open Layout for A4 Print / PDF Save
            </button>
            """
            st.components.v1.html(print_button_html, height=50)

        # -------------------------------------------------------------
        # DATA METRICS BREAKDOWN
        # -------------------------------------------------------------
        st.markdown("---")
        st.subheader("📊 Workload Fairness & Balance Breakdown")
        
        breakdown = []
        for t in teachers:
            teach_hrs = precise_teaching_minutes.get(t, 0) / 60.0
            sb_hrs = sum(solver.Value(standby[(t, d, s)]) for d in range(6) for s in range(operating_hours[d][0], operating_hours[d][1])) / 2.0
            
            breakdown.append({
                "Teacher": t,
                "Teaching (hrs)": round(teach_hrs, 2),
                "Standby (hrs)": round(sb_hrs, 1),
                "Total Workload (hrs)": round(teach_hrs + sb_hrs, 2)
            })
            
        b_df = pd.DataFrame(breakdown).sort_values(by="Teacher")
        st.dataframe(b_df, width='stretch')
        
    else:
        st.error("No feasible schedule found. Increasing minimum standby requirements or lowering maximum staffing numbers can sometimes make a timetable mathematically impossible to solve. Try relaxing your parameters in the sidebar and regenerating.")
