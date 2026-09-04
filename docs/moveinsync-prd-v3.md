# Product Requirement Document (PRD)
## MoveInSync-AIR: Multi-Persona Agentic Intelligence & Reporting Layer

**Document Version:** v3.0  
**Date:** September 4, 2026  
**Status:** Approved / Release Candidate for Hackathon Build  
**Author:** Senior Product Owner, Team Sudo  

---

### 1. Executive Summary & Core Product Vision

In large-scale enterprise mobility operations, managing thousands of daily employee commutes across multi-vendor fleets produces an overwhelming, continuous stream of transactional data [21, 22]. Traditional operations rely on static dashboards that require manual inspection to spot anomalies, creating delayed responses, cost leaks, and safety risks [23, 37, 85, 87].

**MoveInSync-AIR (Agentic Intelligence & Reporting)** introduces an autonomous cognitive layer that **Senses, Reasons, and Acts** [23, 66, 68]. By unifying disparate operational tables, the platform detects critical incidents and cost leaks, computes their business and financial consequences, and triggers closed-loop communications or leadership-ready reporting with zero manual overhead [23, 26].

Acknowledging the official PDF statement allowing solutions to serve **one or more** personas, MoveInSync-AIR implements a **role-switchable intelligence center** that supports all three corporate stakeholders: the **Transport Manager**, the **Line Manager**, and the **Transport & Facilities Head** [24, 25, 35].

---

### 2. The Multi-Persona Context Switcher (Bonus Feature)

To capture the **礼物 BONUS Feature** points for multi-persona support, the application implements a seamless, no-login Role Switcher in the top navigation bar [28, 35]. Switching roles triggers an immediate database-level context re-scoping [35]:

*   **Role 1: Transport Manager (Operational Context):** Limits data scopes to active, same-day routes, pending driver exceptions, and safety alarms [24, 69, 86].
*   **Role 2: Line Manager (Team Context):** Restricts query schemas using a worker roster mapping (`emp_data.stwid` linked to organizational line managers) to display safety, punctuality, and attendance correlations solely for their team [25, 39, 69].
*   **Role 3: Transport & Facilities Head (Strategic Context):** Opens a global, unrestricted view across all sites, billing cycles, vendor contracts, and emissions ledgers to power strategic long-term decisions [25, 26, 70].

---

### 3. Detailed Feature-by-Feature Blueprint

Every feature outlined below maps directly to one of the six official **Solution Forms** and implements a rigorous **Sense ➔ Reason ➔ Act** execution pattern [23, 26, 73, 89].

---

### FEATURE 1: Escort Compliance & Real-time Safety Monitor
*   **Target Persona:** Transport Manager [24, 69, 86]
*   **Solution Form:** Proactive Alerting & Triggers + Automated Communications [89]
*   **Underlying Data Fields:**
    *   `alerts_data`: `event_type` (`WOMAN_TRAVELLING_ALONE`, `PANIC_MOBILE`), `severity` (`Sev-1`), `acknowledge_time`, `start_time` [1]
    *   `ride_data_trip`: `actual_escort` (bool), `actual_start_epoch`, `trip_direction` [7]
    *   `emp_data`: `gender` [5]
*   **Sense:**  
    Detects a late-night `LOGOUT` (drop) trip starting (`ride_data_trip.actual_start_epoch`) carrying a female employee (`emp_data.gender = FEMALE`) where `ride_data_trip.actual_escort = False` (violating regulatory compliance rules), OR detects any active `Sev-1` alert (`alerts_data.event_type = PANIC_MOBILE`) [1, 7].
*   **Reason:**  
    Determines if the unescorted drop is an active threat based on historical route safety ratings (`trip_feedback.safety_rating < 3.0`) and the vendor's past incident frequency [9, 10]. It calculates the minutes of unacknowledged exposure.
*   **Act (HITL workflow):**  
    Pushes an emergency alert to the Transport Manager's live queue [38]. Simultaneously, the actor drafts an urgent warning notice to the vendor’s dispatch desk [43]. The agent **pauses execution (LangGraph `interrupt()`)**, waiting for manual manager verification before sending [38, 43].
*   **How to Implement:**  
    An asynchronous background thread monitors incoming `alerts_data` and active trips in `ride_data_trip`. A LangGraph node checks the escort condition, triggers `interrupt()`, and publishes the state to the React frontend over a WebSockets channel for an immediate visual popup in the manager's UI [46, 47, 50].
*   **Measurement Protocol:**  
    $$\text{Critical Response Time (s)} = \text{Average}(\text{alerts\_data.acknowledge\_time} - \text{alerts\_data.start\_time})$$  
    $$\text{Late-Night Female Safety Escort Compliance (\%)} = \left( 1 - \frac{\sum (\text{trip\_direction = 'LOGOUT'} \land \text{gender = 'FEMALE'} \land \text{actual\_escort = False})}{\text{Total Late-Night Female Trips}} \right) \times 100$$
*   **Expected Outcome:**  
    Reduce emergency response and alert acknowledgment lag to **under 60 seconds**; maintain **100% compliance** on late-night female transport escorts.

---

### FEATURE 2: Billing Slab & Distance Discrepancy Auditor
*   **Target Persona:** Transport & Facilities Head [25, 70, 86]
*   **Solution Form:** Insight & Anomaly Detection [89]
*   **Underlying Data Fields:**
    *   `bill_data`: `slab_name` (e.g., "Long", "Medium"), `total_trip_km`, `trip_cost`, `trip_id` [3]
    *   `ride_data_trip`: `traveled_km`, `vendor_id`, `trip_id` [7]
*   **Sense:**  
    Scans every billing cycle line item (`bill_data`) to identify trips where the billed distance (`bill_data.total_trip_km`) or applied tier (`bill_data.slab_name`) does not align with the actual GPS traveled distance (`ride_data_trip.traveled_km`) [3, 7].
*   **Reason:**  
    Cross-references actual travel distance against the contract's defined slab thresholds (e.g., flagging instances where a short trip under 15 km was billed under a premium "Long" slab requiring >25 km) [3, 7]. It calculates the exact monetary value of the overbilling.
*   **Act:**  
    Aggregates all flagged trips into an interactive **Billing Dispute Log**, auto-generates a formatted markdown chargeback memo for the underperforming vendor, and posts the audit highlights directly to the Strategic dashboard [3, 37].
*   **How to Implement:**  
    A Python scheduled cron script normalized with the `trip_id` string replacement rules strips commas and joins `bill_data` and `ride_data_trip` [19, 20]. The script computes distance differentials, applies contract pricing tables, and inserts disputed transactions into a `data_quality_flags` database [19, 53].
*   **Measurement Protocol:**  
    $$\text{Billing Slab Discrepancy Amount (\text{₹})} = \sum_{\text{Disputed Trips}} \left( \text{bill\_data.trip\_cost} - \text{Calculated Slab Cost(ride\_data\_trip.traveled\_km)} \right)$$  
    $$\text{Billing Leakage Rate (\%)} = \left( \frac{\text{Discrepancy Amount}}{\text{Total Billed Fleet Spend}} \right) \times 100$$
*   **Expected Outcome:**  
    Recover **8% to 12% of total fleet spend** from incorrect vendor billing; reduce audit cycle times from 15 days to **under 5 minutes**.

---

### FEATURE 3: Commute-Attendance Correlation Engine
*   **Target Persona:** Line Manager [25, 69, 86]
*   **Solution Form:** Insight & Anomaly Detection + Automated Communications [89]
*   **Underlying Data Fields:**
    *   `emp_data`: `is_no_show` (bool), `boarding_status`, `not_boarding_reason`, `stwid`, `trip_id` [5]
    *   `ride_data_trip`: `delay_minutes`, `delay_reason`, `trip_id` [7]
*   **Sense:**  
    Monitors high-frequency delays (`ride_data_trip.delay_minutes`) and links them with employee no-show markers (`emp_data.is_no_show`) and not-boarding reasons [5, 7].
*   **Reason:**  
    Determines whether a shift tardiness or absenteeism event was structurally caused by transport fleet delay (e.g., `delay_reason = DRIVER` or `TRAFFIC`) or represents employee-side negligence (`is_no_show = True` with no route delay) [5, 7, 32]. This prevents unfair attendance penalties and isolates chronic employee offenders.
*   **Act:**  
    For employees flagged with 3+ unexcused no-shows in a single cycle, the agent autonomously drafts a warning reminder and a calendar invitation to verify booking details [5, 43].
*   **How to Implement:**  
    The SQL Agent joins `emp_data` and `ride_data_trip` on `trip_id` [17]. A grouping function compiles individual `stwid` profiles to identify no-show counts [5, 17, 19]. The communication generator integrates with an SMTP service node to trigger emails [14, 43].
*   **Measurement Protocol:**  
    $$\text{Team No-Show Rate (\%)} = \left( \frac{\sum (\text{emp\_data.is\_no\_show} = \text{True})}{\text{Total Planned Team Trip Bookings}} \right) \times 100$$  
    $$\text{Wasted Fleet Capacity Expense (\text{₹})} = \sum_{\text{no-shows}} \left( \frac{\text{bill\_data.trip\_cost}}{\text{ride\_data\_trip.actual\_cab\_capacity}} \right)$$
*   **Expected Outcome:**  
    **Reduce team no-show rates by 25%** through automated reminders; reclaim **15% of underutilized fleet seating capacity** (lowering the average cost-per-employee) [26].

---

### FEATURE 4: Interactive Conversational Mobility Copilot
*   **Target Persona:** Multi-Persona (Transport Manager, Line Manager, Transport Head) [24, 25]
*   **Solution Form:** Conversational Agent (NL Q&A on Mobility Data) [89]
*   **Underlying Data Fields:** Unified joins across all five tables (`ride_data_trip`, `emp_data`, `trip_feedback`, `alerts_data`, `bill_data`) [17]
*   **Sense:**  
    Accepts natural language operational queries from users (e.g., *"Show me which office location had the highest safety incident rates last month"* or *"What was our average cost per employee on the evening shift?"*) [20, 24, 25, 41].
*   **Reason:**  
    The SQL Agent cluster parses the user request, introspects the database DDL and metadata, generates syntactically correct SQL, executes the query, and formats the raw numbers into a clear narrative with trend context and business implications [26, 48].
*   **Act:**  
    Displays the text answer on the chat interface alongside the generated SQL block for transparency, allowing users to verify that the numbers are strictly grounded in the database [49].
*   **How to Implement:**  
    Built as a LangGraph node cluster leveraging a robust SQL generation loop: `list_tables` ➔ `get_schema` (generating full CREATE TABLE DDL + 2-3 sample rows) ➔ `generate_query` ➔ `pre_flight_syntax_check` (local validation via `sqlglot` to bypass broken syntax) ➔ `run_query` ➔ `error_loop` (max 3 retries) [48, 49].
*   **Measurement Protocol:**  
    $$\text{SQL Generation Accuracy (\%)} = \left( \frac{\text{Queries Executed without Syntax or Logic Errors}}{\text{Total Natural Language Queries Submitted}} \right) \times 100$$  
    $$\text{Inference Latency (s)} = \text{Average Timestamp}(\text{Agent Response}) - \text{Timestamp}(\text{User Message})$$
*   **Expected Outcome:**  
    Achieve **95%+ SQL query generation accuracy** on first attempts; deliver fully contextualized answers in **under 3.0 seconds**.

---

### FEATURE 5: Green Fleet Transition Ledger & Sustainability Monitor
*   **Target Persona:** Transport & Facilities Head [25, 70, 86]
*   **Solution Form:** Decision-Support Dashboard + Automated Reporting [89]
*   **Underlying Data Fields:**
    *   `ride_data_trip`: `actual_cab_fuel_type` (`Electric`, `Diesel`, `Petrol`), `traveled_km`, `vendor_id` [7]
    *   `sustainability_targets` (Static benchmark references): baseline target of 82 gCO2 per passenger-km [55, 56]
*   **Sense:**  
    Monitors daily kilometers traveled, grouped by vehicle fuel type (`actual_cab_fuel_type`) across all active routing and vendor logs [7, 8].
*   **Reason:**  
    Applies standard carbon-emissions coefficients (e.g., Diesel = 170g $CO_2$/km, Petrol = 150g $CO_2$/km, Electric = 0g $CO_2$/km) to the telemetry logs [51]. It calculates total emitted carbon, compares performance against sustainability targets, and evaluates EV contract cost differences [3, 7, 26, 53].
*   **Act (Bonus Output):**  
    Generates a **Forward-Ready Sustainability & Carbon Offset Memo** in pristine markdown format that details which routes are prime for EV transition, allowing the Facilities Head to email it to leadership untouched [26, 27, 43, 71].
*   **How to Implement:**  
    A backend reporting node queries `ride_data_trip` and aggregates mileage by fuel type [7]. A visualization service utilizes `matplotlib` (configured headlessly) to render trend line-charts which are embedded directly into the generated markdown reports [31, 56].
*   **Measurement Protocol:**  
    $$\text{Carbon Offset Created (kg } CO_2) = \sum_{\text{EV Trips}} \left( \text{traveled\_km} \times \text{ICE Baseline Emissions Coefficient (82g/km)} \right)$$  
    $$\text{EV Fleet Adoption Rate (\%)} = \left( \frac{\text{Distance Traveled in Electric Cabs}}{\text{Total Fleet Distance Traveled}} \right) \times 100$$
*   **Expected Outcome:**  
    Displace **18 to 22 tonnes of CO2 per quarter**; increase electric vehicle (EV) fleet utilization share by **15% MoM**.

---

### 4. Technical Ingestion Specification (Messy Data Ingestion)

To secure the **Good-To-Have** points for handling messy data gracefully, the Sensor Layer must pass all files through a deterministic python preprocessing pipeline prior to database storage [19, 27, 41, 47, 71]:

1.  **Uniform Join-Key Coercion:**
    *   *Problem:* `trip_id` is represented as a comma-string in some tables and an integer in others [19].
    *   *Resolution:* Uniformly strip commas and cast `trip_id` and `stwid` to `int64` using a standardized function:
        ```python
        df["trip_id"] = df["trip_id"].astype(str).str.replace(",", "", regex=False).astype("int64")
        ```
2.  **Rider-Key Normalization:**
    *   *Problem:* System placeholder riders (`stwid = 0` or `"0"`) skew average performance metrics [2, 19].
    *   *Resolution:* Explicitly filter out `stwid = 0` from safety compliance and feedback averages, reserving them strictly for overall trip-level logs [2, 6, 19].
3.  **Invalid Distance Resolution:**
    *   *Problem:* `planned_km` and `traveled_km` in `emp_data` contain impossible negative float values (e.g., `-6.63`) [5, 6, 19].
    *   *Resolution:* Clip all negative entries to `0.0` and flag the row under a `data_quality_flags` ledger for operational tracking [19, 53].
4.  **Date & Epoch Harmonization:**
    *   *Problem:* Highly inconsistent timestamp formats across all five tables [19].
    *   *Resolution:* Reconcile and parse commas and datetimes using unified epoch timestamp parsing [19].
5.  **Severity Data Cleanup:**
    *   *Problem:* The `alerts_data.severity` column contains a stray string literal `"False"` alongside 16,348 null values [1, 19].
    *   *Resolution:* Coerce all occurrences of `"False"` to `null` to ensure SQL queries can execute clean counts grouped by `Sev-1/2/3` [1, 19].

---

### 5. Expected Outcomes & Quantified Business Impact

| Strategic Goal | Target KPI Metric | Dataset Mapping | Expected Target |
| :--- | :--- | :--- | :--- |
| **Punctuality & Reliability** | On-Time Arrival (OTA) Rate with delay-reason context [30] | `ride_data_trip.delay_minutes`<br>`ride_data_trip.delay_reason` [7] | **Maintain >95% OTA** across morning shifts; reduce driver-caused delay exceptions by **25%** [56]. |
| **Cost Savings** | Preventative invoice leakage and slab auditing [31] | `bill_data.slab_name`<br>`ride_data_trip.traveled_km` [3, 7] | **Recover 8%–12% of billing spend** by eliminating billing for incorrect distance slabs. |
| **Fleet Seating Optimization** | Seating occupancy and capacity utilization [32] | `ride_data_trip.actual_cab_capacity`<br>`emp_data.is_no_show` [5, 7] | **Improve average cab utilization by 15%**; decrease cost-per-employee by routing high-occupancy trips. |
| **Safety Compliance** | Emergency acknowledgment and escort rates [31] | `alerts_data.event_type`<br>`ride_data_trip.actual_escort` [1, 7] | **Reduce incident acknowledgment lag to < 60 seconds**; maintain **0 unescorted** female late-night trips. |
| **Corporate Sustainability** | Fuel carbon displacement (CO2) [31] | `ride_data_trip.actual_cab_fuel_type`<br>`ride_data_trip.traveled_km` [7] | **Displace 18–22 tonnes of CO2 per quarter** by routing higher shares of electric vehicles. |
