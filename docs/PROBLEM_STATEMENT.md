# MoveInSync Hackathon: Problem Statement

## Video Introduction

Watch the problem statement explained by Udual Trii, VP Product & Growth at MoveInSync: [Problem Explanation Video](https://drive.google.com/file/d/1B7nLPnQuZwYTr6PoTwAd_5PAcJCsFz-l/view)

---

## The Company Context

**MoveInSync** is the world's largest office, commute, and workplace management platform. We provide comprehensive employee journey management from the moment they leave their doorstep until they safely return home.

### Scale & Impact

- **1M+ employees** managed daily
- **400+ organizations** relying on our platform
- **120+ Fortune 500 companies** as clients
- Managing complete employee journeys: home → office → workplace → home

---

## The Challenge

### The Problem

Every day, MoveInSync generates massive amounts of transportation and mobility data. This data includes critical variables around:

- **Timeliness** - Are employees arriving on schedule?
- **Safety** - Are journeys secure and incident-free?
- **Cost** - What is the efficiency of transportation spend?
- **Vendor Performance** - How are logistics partners performing?
- **Carbon Emissions** - What is the environmental impact?

However, **live data streams 24/7**, and traditional dashboards cannot keep pace with the volume and velocity. Decision-makers need something smarter.

### The Solution We Need

**Build an agentic intelligence layer that can:**

1. **SENSE** - Understand what is happening in real-time transportation data
2. **REASON** - Determine why events are occurring and what they mean
3. **ACT** - Take autonomous or semi-autonomous actions to optimize outcomes

This layer should require **minimal human prompting or interaction** while providing deep insights and driving business outcomes.

---

## Target Personas

The solution must serve one or more of these three personas with tailored insights and actions:

### 1. Transport Manager
**Primary Focus:** Real-time operational metrics and alerts

- Needs to know if routes are on schedule
- Requires immediate alerts for deviations
- Wants to optimize routes and vendor performance
- Uses data for daily tactical decisions

**Key Metrics:**
- On-time performance by route
- Active incident tracking
- Vendor SLA compliance
- Daily cost summary

### 2. Line Manager
**Primary Focus:** Team-level compliance and impact

- Needs to track employee commute compliance
- Wants visibility into team transportation costs
- Cares about commute time impact on work hours
- Uses data for team management and safety

**Key Metrics:**
- Team commute patterns
- Attendance correlation with transport issues
- Team cost allocation
- Safety incident reports (team-level)

### 3. Transport Head
**Primary Focus:** Strategic insights and performance trends

- Needs executive dashboards and trend analysis
- Makes policy and partnership decisions
- Focuses on long-term cost optimization
- Responsible for sustainability goals

**Key Metrics:**
- Monthly/quarterly trend analysis
- Vendor performance scorecards
- Carbon footprint tracking
- Cost per employee metrics
- Compliance and risk summaries

---

## Requirements Framework

### 🔴 MANDATORY Requirements (All 4 Required)

1. **Runs on Sample Dataset**
   - Solution must execute against provided or sample transportation data
   - No requirement for real-time integration (though bonus if included)

2. **Demonstrates Sense → Reason → Act**
   - **Sense:** System identifies patterns, anomalies, or significant events in data
   - **Reason:** System analyzes root causes and business implications
   - **Act:** System takes action (autonomous alerts, recommendations, or triggered processes)

3. **Serves One of Three Personas**
   - Solution must be tailored to transport manager, line manager, or transport head
   - Should include persona-specific insights and recommendations
   - Actions should align with persona's responsibilities

4. **Every Metric Carries Context**
   - Raw numbers alone are insufficient
   - Each metric must include: business impact, trend direction, severity/urgency
   - Example: Not just "Cost: $50K" but "Cost: $50K (+5% vs. last month, investigate vendor X price increase)"

### 🟡 GOOD-TO-HAVE Features (Optional but Valued)

1. **Handles Messy Data**
   - Real transportation data is often incomplete or inconsistent
   - System should gracefully handle missing values, duplicates, formatting issues
   - Should flag data quality issues appropriately

2. **Triggers Autonomously**
   - System detects important situations and acts without explicit prompting
   - OR combines 2+ solution approaches (e.g., predictive + optimization)
   - Shows proactive vs. reactive intelligence

3. **Leadership-Ready Output**
   - Reports or insights that an executive could forward to leadership untouched
   - Professional presentation, clear narrative, actionable insights
   - No manual polishing required

### 🎁 BONUS Features (Nice to Have)

- Multi-persona support with switching contexts
- Real-time data ingestion and streaming
- Integration with external data sources (weather, traffic, events)
- Predictive analytics (forecasting delays, cost anomalies)
- Natural language interaction with the agent
- Compliance and audit trail generation
- Mobile-friendly dashboards
- Integration with enterprise systems (HRMS, analytics platforms)

---

## Technology Stack

### Preferred Stack (Not Required)
- **Backend:** Java
- **Frontend:** Angular
- **Infrastructure:** AWS

### Any Stack Welcome
You're free to use any technology stack that accomplishes the goals. Focus on solving the problem, not technology choice.

---

## Success Criteria & Judging

### Does It Work?
- Solution runs without errors on sample data
- All mandatory requirements are demonstrably met
- Agent successfully senses, reasons, and acts

### Does It Land?
- Solution is clearly valuable to the target persona
- Insights drive real business decisions
- Actions are appropriate and well-reasoned
- User experience is intuitive

### Can It Scale?
- Architecture supports enterprise scale
- Code is maintainable and well-documented
- Performance is acceptable for production use
- Handles growing data volumes gracefully

---

## Data Domains & Metrics

### Timeliness
- Route on-time performance
- Delay frequency and duration
- Peak delay periods
- Trend analysis (improving/deteriorating)

### Safety
- Incident reports (accidents, violations)
- Safety trend indicators
- Risk zones (geographies, times)
- Driver/vendor safety ratings

### Cost
- Per-route costs
- Per-employee costs
- Vendor cost comparison
- Cost trend analysis
- Cost anomalies and variances

### Vendor Performance
- SLA compliance rates
- Quality metrics
- Reliability scores
- Cost efficiency
- Trend trajectory

### Carbon Emissions
- Total carbon footprint
- Per-route emissions
- Emissions trend (improving/worsening)
- Comparison to sustainability goals
- Optimization opportunities

---

## Example Agent Scenarios

### Transport Manager Agent
**Scenario:** Daily morning briefing

1. **SENSE:** Analyze overnight route scheduling, weather, historical patterns
2. **REASON:** Identify routes with high delay probability; flag vendor performance issues
3. **ACT:** Send alerts to dispatch team, suggest backup vendors, recommend route adjustments

### Line Manager Agent
**Scenario:** Weekly team compliance review

1. **SENSE:** Track team member commute data, attendance patterns, costs
2. **REASON:** Correlate commute issues with absences; identify cost outliers
3. **ACT:** Flag compliance issues, suggest commute assistance, highlight team cost control

### Transport Head Agent
**Scenario:** Monthly strategic review

1. **SENSE:** Aggregate monthly performance across all routes, vendors, regions
2. **REASON:** Identify strategic trends, vendor performance, carbon impact, ROI
3. **ACT:** Generate executive report, recommend vendor changes, suggest policy updates

---

## Deliverables

### Required
- Working solution running on sample data
- Clear demonstration of sense → reason → act flow
- Documentation explaining agent logic and decision-making

### Recommended
- Sample dataset or data generation script
- API/interface for interacting with the agent
- Persona-focused dashboard or report
- Instructions for running and testing

### Nice-to-Have
- Docker setup for easy deployment
- Unit and integration tests
- Architecture diagrams
- Performance metrics and benchmarks

---

## Getting Started

1. **Review This Document** - Understand the problem, personas, and requirements
2. **Choose Your Persona** - Focus on one target user group
3. **Design Your Agent** - Plan sense → reason → act flow
4. **Implement & Test** - Build on sample data, validate against requirements
5. **Document & Demo** - Prepare clear explanation and demonstration

---

## Important Notes

- **Stack is flexible** - Use what you're comfortable with
- **Focus on the problem** - Not just the technology
- **Start simple** - A well-reasoned simple solution beats a complex one
- **Iterative improvement** - MVP first, then add good-to-have features
- **Persona is key** - Tailor everything to who you're building for

Good luck! Let's build agents that can act! 🚀
