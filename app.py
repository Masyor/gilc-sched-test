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

def get_teacher_styles(name):
    """Generates an individual, unique, stable Tailwind-like style for each unique teacher name."""
    if not name or name.strip() in ["", "—"]:
        return "color: #94a3b8; font-style: italic;"
    
    hash_digest = hashlib.md5(name.encode('utf-8')).hexdigest()
    hue = int(hash_digest[:4], 16) % 360
    
    bg = f"hsl({hue}, 60%, 95%)"
    text = f"hsl({hue}, 70%, 25%)"
    border = f"hsl({hue}, 50%, 85%)"
    
    return f"background-color: {bg}; color: {text}; border: 1px solid {border}; display: inline-block; padding: 2px 8px; margin: 2px 0; border-radius: 6px; font-weight: 500; font-size: 12px; width: 100%; box-sizing: border-box;"

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

proximity_limit_hours = st.sidebar.slider(
    "Proximity to Class Limit (Hours)", 
    min_value=0.5, max_value=8.0, value=2.0, step=0.5,
    help="Teachers will not be assigned a standby slot unless it falls within this number of hours of one of their teaching classes on that day."
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
    min_standby_slots = int(min_standby_hours * 2)
    proximity_limit_slots = int(proximity_limit_hours * 2)
    MAX_DAILY_SPAN_SLOTS = 18  # 9 hours * 2 blocks per hour

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
                # Rule 1: If busy teaching, standby is impossible
                if s in busy[t][d]:
                    standby[(t, d, s)] = model.NewIntVar(0, 0, f"sb_{t}_{d}_{s}")
                    continue
                
                # Rule 2: Span and Proximity Constraints
                if len(busy[t][d]) > 0:
                    # Enforce New 9-Hour Rule: Max distance between any two active elements on day 'd' can't exceed 9 hours
                    max_span_violates = any(abs((s + 1) - class_slot) > MAX_DAILY_SPAN_SLOTS or abs(s - (class_slot + 1)) > MAX_DAILY_SPAN_SLOTS for class_slot in busy[t][d])
                    if max_span_violates:
                        standby[(t, d, s)] = model.NewIntVar(0, 0, f"sb_span_{t}_{d}_{s}")
                        continue

                    # Proximity filter
                    min_distance_to_class = min(abs(s - class_slot) for class_slot in busy[t][d])
                    if min_distance_to_class > proximity_limit_slots:
                        standby[(t, d, s)] = model.NewIntVar(0, 0, f"sb_prox_{t}_{d}_{s}")
                        continue
                else:
                    # If the teacher has zero classes scheduled on this entire day, they shouldn't do standby
                    standby[(t, d, s)] = model.NewIntVar(0, 0, f"sb_noclass_{t}_{d}_{s}")
                    continue
                
                standby[(t, d, s)] = model.NewBoolVar(f"sb_{t}_{d}_{s}")
                    
    for d in range(6):
        start_op, end_op = operating_hours[d]
        for s in range(start_op, end_op):
            model.Add(sum(standby[(t, d, s)] for t in teachers) >= 1)
            model.Add(sum(standby[(t, d, s)] for t in teachers) <= max_staff_per_slot)
            
    for t in teachers:
        sb_sum_expr = sum(
            standby[(t, d, s)] 
            for d in range(6) 
            for s in range(operating_hours[d][0], operating_hours[d][1]) 
            if (t, d, s) in standby
        )
        model.Add(sb_sum_expr >= min_standby_slots)
            
    for t in teachers:
        for d in range(6):
            free_blocks = []
            for start_s in range(22, 27):
                b1_busy = start_s in busy[t][d]
                b2_busy = (start_s + 1) in busy[t][d]
                if b1_busy or b2_busy:
                    continue
                block_free = model.NewBoolVar(f"free_{t}_{d}_{start_s}")
                if (t, d, start_s) in standby and (t, d, start_s + 1) in standby:
                    model.Add(standby[(t, d, start_s)] + standby[(t, d, start_s + 1)] == 0).OnlyEnforceIf(block_free)
                    free_blocks.append(block_free)
            if free_blocks:
                model.Add(sum(free_blocks) >= 1)
                
    total_teaching_slots = {t: sum(len(busy[t][d]) for d in range(6)) for t in teachers}
    total_workload = {}
    for t in teachers:
        total_workload[t] = model.NewIntVar(0, 1000, f"workload_{t}")
        sb_sum = sum(
            standby[(t, d, s)] 
            for d in range(6) 
            for s in range(operating_hours[d][0], operating_hours[d][1]) 
            if (t, d, s) in standby
        )
        model.Add(total_workload[t] == total_teaching_slots[t] + sb_sum)
        
    max_workload = model.NewIntVar(0, 1000, "max_workload")
    min_workload = model.NewIntVar(0, 1000, "min_workload")
    for t in teachers:
        model.Add(max_workload >= total_workload[t])
        model.Add(min_workload <= total_workload[t])
        
    transitions = []
    for t in teachers:
        for d in range(6):
            start_op, end_op = operating_hours[d]
            for s in range(start_op, end_op - 1):
                if (t, d, s) not in standby or (t, d, s+1) not in standby:
                    continue
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
                assigned = [t for t in teachers if (t, d, s) in standby and solver.Value(standby[(t, d, s)]) == 1]
                time_str = f"{s//2:02d}:{(s%2)*30:02d} - {(s+1)//2:02d}:{((s+1)%2)*30:02d}"
                day_str = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][d]
                res_data.append({"Day": day_str, "Time": time_str, "Standby": ", ".join(assigned)})
                
        res_df = pd.DataFrame(res_data)
        days_order = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
        
        planner_pivot = res_df.pivot(index='Time', columns='Day', values='Standby').fillna("")
        planner_pivot = planner_pivot.reindex(columns=days_order).fillna("")

        # -------------------------------------------------------------
        # TAILWIND CSS & INDIVIDUAL BADGE HTML GENERATOR 
        # -------------------------------------------------------------
        st.markdown("---")
        st.subheader("🗓️ Master Weekly Standby Planner Grid")
        
        html_output = """
        <div id="printable-planner" style="font-family: ui-sans-serif, system-ui, sans-serif; background-color: #f8fafc; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);">
            <table style="width: 100%; border-collapse: collapse; text-align: left; font-size: 13px;">
                <thead>
                    <tr style="background-color: #0f172a; color: #f8fafc;">
                        <th style="padding: 14px; font-weight: 600; border-bottom: 2px solid #334155; text-align: center; width: 12%;">Time</th>
        """
        for day in days_order:
            html_output += f'<th style="padding: 14px; font-weight: 600; border-bottom: 2px solid #334155; text-align: center;">{day}</th>'
        html_output += "</tr></thead><tbody>"
        
        row_alt = False
        for time_slot, row in planner_pivot.iterrows():
            bg_color = "#f1f5f9" if row_alt else "#ffffff"
            row_alt = not row_alt
            
            html_output += f'<tr style="background-color: {bg_color}; border-bottom: 1px solid #e2e8f0; transition: background-color 0.15s ease;" onmouseover="this.style.backgroundColor=\'#f1f5f9\'" onmouseout="this.style.backgroundColor=\'{bg_color}\'">'
            html_output += f'<td style="padding: 10px; font-weight: 600; color: #334155; text-align: center; background-color: #e2e8f0; border-right: 1px solid #cbd5e1; font-variant-numeric: tabular-nums;">{time_slot}</td>'
            
            for day in days_order:
                cell_value = row[day]
                html_output += '<td style="padding: 8px; vertical-align: top; min-width: 120px; border-right: 1px solid #e2e8f0;">'
                
                if not cell_value.strip():
                    html_output += '<div style="color: #94a3b8; text-align: center; font-style: italic; padding-top: 4px;">—</div>'
                else:
                    individual_names = [n.strip() for n in cell_value.split(",") if n.strip()]
                    for name in individual_names:
                        style_rule = get_teacher_styles(name)
                        html_output += f'<div style="{style_rule}">{name}</div>'
                        
                html_output += '</td>'
            html_output += '</tr>'
            
        html_output += "</tbody></table></div>"
        
        st.components.v1.html(html_output, height=650, scrolling=True)

        # -------------------------------------------------------------
        # EXPORTS AND ACTIONS
        # -------------------------------------------------------------
        col1, col2 = st.columns(2)
        with col1:
            pretty_csv = planner_pivot.to_csv(index=True)
            st.download_button(
                label="📥 Download Pretty Grid Planner (CSV)", 
                data=pretty_csv, 
                file_name="weekly_matrix_planner.csv", 
                mime="text/csv"
            )
            
        with col2:
            print_button_html = f"""
            <script>
            function printPlanner() {{
                var printWindow = window.open('', '_blank');
                var gridContent = window.parent.document.getElementById("printable-planner") || window.parent.document.querySelector('iframe').contentDocument.getElementById("printable-planner");
                
                var payloadHtml = gridContent ? gridContent.outerHTML : `{html_output}`;
                
                printWindow.document.write('<html><head><title>Print Standby Schedule</title>');
                printWindow.document.write('<style>@page {{ size: A4 landscape; margin: 10mm; }} body{{font-family:system-ui,sans-serif; margin:0; padding:0;}} table{{width:100%; border-collapse:collapse;}} th,td{{padding:6px !important; border:1px solid #cbd5e1 !important;}} div{{box-shadow:none !important; border-radius:0 !important;}}</style>');
                printWindow.document.write('</head><body>');
                printWindow.document.write('<h2 style="color:#0f172a; margin-bottom:12px;">Master Weekly Standby Planner Grid</h2>');
                printWindow.document.write(payloadHtml);
                printWindow.document.write('<script>window.onload = function() {{ setTimeout(function() {{ window.print(); window.close(); }}, 300); }}</sc' + 'ript></body></html>');
                printWindow.document.close();
            }}
            </script>
            <button onclick="printPlanner()" style="width:100%; padding: 0.5rem; background-color: #0f172a; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-family: sans-serif; transition: background 0.2s;">
                🖨️ Save A4 Landscape PDF / Print
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
            sb_hrs = sum(solver.Value(standby[(t, d, s)]) for d in range(6) for s in range(operating_hours[d][0], operating_hours[d][1]) if (t, d, s) in standby) / 2.0
            
            breakdown.append({
                "Teacher": t,
                "Teaching (hrs)": round(teach_hrs, 2),
                "Standby (hrs)": round(sb_hrs, 1),
                "Total Workload (hrs)": round(teach_hrs + sb_hrs, 2)
            })
            
        b_df = pd.DataFrame(breakdown).sort_values(by="Teacher")
        st.dataframe(b_df, width='stretch')
        
    else:
        st.error("No feasible schedule found. Adding a max 9-hour workday span constraint limits availability significantly. Try relaxing your sidebar configuration parameters.")
