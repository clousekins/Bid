Above is the job description. Below is the candidate profile. Tailor the resume to the job description.

Brian (Yi) Gou | Senior Data Engineer
Tustin, California | briangou9@gmail.com | (929) 547 6426 | https://www.linkedin.com/in/brian-gou-5838541a7/

#### Keep this contact info exactly as written. Do not alter name, email, phone, location, or LinkedIn. ####

---

SUMMARY
- "summary" must be one professional paragraph in sentence form (not an array). DON'T add the tech stack and just showing my passion, background(DE), collaboration skills
- "summary" should be written in 2 or 3 sentences that show the meaningful and big overview of my experience, not mention about tech stack, just showing the high level of my work and start with 10 years of experience.

---

WORK EXPERIENCE

Senior Data Engineer | UnitedHealth Group | Los Angeles, California | Feb 2023 - Present
   - Focus: Multi Source Integration, Architecture, paralleization, schema drift handling, modernization, ML feature pipelines(depending on the JD), governance, env setup(e.g., dev, uat, prod), Crossfunctional collaboration.
   - Allowed tech: Modern datastack aligining with the JD.
   - Number of Bullet : 7

Data Engineer | Meta | Menlo Park, California | Aug 2022 - Feb 2023
   - Focus: Distributed systems, big data at scale, analytics readiness, Spark pipelines/streaming depeding on the JD.
   - Allowed tech: Modern data stack aliginig with JD.
   - Number of Bullet : 6

Data Engineer | Amazon | San Francisco, California | Oct 2021 - May 2022
   - Focus: Cloud-first pipelines, automation, large-scale data, performance, Spark introduction.
   - Allowed tech: AWS Glue, Lambda, S3, Redshift, Athena, Step Function, Python, Spark (initial), Terraform, other modern stack depending on the JD.
   - Number of Bullet : 6

SQL Data Engineer | UnitedHealth Group | Los Angeles, California | Sep 2017 - Oct 2021
   - Focus: Enterprise ETL ownership, claims data, data quality, validation frameworks, early cloud migration.
   - Allowed tech: python based data engineering, and also classical data stack depending on the JD and cloud native data engineering.
   - Number of Bullet : 4

SQL Developer | Tenet Healthcare | Dallas, Texas | Sep 2016 - Aug 2017
   - Focus: SQL fundamentals, ETL basics, legacy systems, reporting, stored procedures, healthcare exposure.
   - Allowed tech: SQL Server, SSIS, SSRS, Stored Procedures, Excel, on-prem databases.
   - Number of Bullet : 4
---
MANDATORY GLOBAL RULES (consolidated)
1. Single Cloud Provider:
   - Detect and select exactly one cloud provider from the Job Description (AWS, Azure, or GCP).
   - All cloud services, skills, and bullets must reference only that provider (no multi-cloud).
2. Technology Realism & Role Constraints:
   - Technologies referenced in bullets must be logically consistent with each role’s allowed tech list (see Experience Content Rules).
   - Do NOT introduce technologies not supported by the company-role lists.
3. Skill-Experience Mapping:
   - Every bullet must reference at least one skill from the "skills" section.
   - At least 75% of listed skills must appear across experience bullets.
4. Tagging:
   - Maximum of 6 total tagged skills across the resume.
5. Numbers & Quantification:
   - Each bullet must include a quantified outcome (performance %, latency %, data volume in MB/GB, cost savings, reliability improvement, etc.). use % rather than percent in the resume
6. Preservation:
   - Do NOT change Qualifications Summary content (if provided), Education, Certifications, company names, job titles, dates, or locations.
7. Illegal Characters:
   - Avoid the following characters in generated resume text where possible: <, >, [, ], \, ;.
8. Tone:
   - Senior, concise, technical, human-readable. ATS-optimized.
9. Bullets for each professional experience:
   - UnitedHealth Group : 7
   - Meta : 6
   - Amazon : 6
   - UnitedHealth Group : 4
   - Tenet Healthcare : 4
10.Highlight tech keywords with <strong> tags, Do not use ** anywhere — use <strong> tags instead.
---

TOP-TOOL MIRRORING RULE (MOST RECENT ROLE)
- Identify the SINGLE most important tool/platform in the JD (for example: Snowflake, Databricks, Azure Synapse/Fabric, BigQuery, Redshift, dbt, Airflow, ADF).
- Make that tool clearly central in the UnitedHealth Group — Senior Data Engineer role via one realistic, minimal integration pattern (no platform sprawl).
- Examples (choose ONE pattern per JD, only if JD points to it):
  • JD = Snowflake → At UnitedHealth Group, land raw data in S3/ADLS/GCS; model curated layers directly in Snowflake (for example, dbt-snowflake or native Snowflake SQL); do NOT introduce a second warehouse. If interop is needed, expose curated data via secure views or data sharing, not duplicate warehouses.  
  • JD = Databricks → Use Delta Lake, Unity Catalog, and either Databricks Workflows OR one external orchestrator (for example, Airflow); avoid combining multiple warehouses (no Snowflake + BigQuery at once).  
  • JD = dbt → Use dbt with exactly ONE warehouse (Snowflake / BigQuery / Redshift / Databricks). If dbt Cloud is used and the JD does not require another orchestrator, do NOT add Airflow or Dagster just to run dbt jobs.  
  • JD = AWS → Emphasize S3, IAM roles, autoscaling compute (EMR / Glue / Databricks on AWS), and cost controls; orchestrate with MWAA, Step Functions, or Databricks Workflows (pick one).  
  • JD = GCP/BigQuery → Land data in GCS, transform and serve in BigQuery; use Dataform, dbt, or Spark (pick one) for transformations; don’t add Snowflake unless the JD explicitly calls for multi-warehouse.  
  • JD = Azure → Use ADLS, Entra ID, Key Vault, and either ADF, Synapse Pipelines, or Fabric Data Factory (pick one orchestrator). If the JD focuses on Azure Databricks, mention Azure Databricks + ADF OR Azure Databricks + Workflows, not both.
- One tool per function family within a role (Streaming, DWH, Orchestration, Transform, BI, IaC). Never introduce a second tool from the same family in the SAME role.

---

STACK MINIMALISM (COST/OPS FIRST)
Pick ONE bundle per role/project. No same-function overlap:
- DWH: Snowflake | BigQuery | Redshift | Synapse/Fabric | None (lake-only)
- Orchestration: Airflow | ADF | Glue | Dataflow | Composer | Databricks Workflows | Prefect | Dagster | Step Functions | MWAA
- Transform: dbt | Dataform | Matillion | Spark notebooks | SQL-only (choose one)
- Streaming: Kafka | Kinesis | Pub/Sub | Event Hubs
- BI: Sigma | Looker | Power BI | Tableau
- IaC: Terraform | CloudFormation | Bicep | Pullumi
- ML Platform: MLflow | SageMaker | Vertex AI | Azure ML

- If using dbt Cloud based on the JD and no other orchestrator is required, do NOT add Airflow/Dagster just to run dbt jobs.  
- If using Workflows on Databricks because the JD mentions it, do NOT also claim Airflow orchestrating the same DAGs.  
- Prefer managed services to reduce ops burden; always mention autoscaling, cost controls, and failure handling.

---

ROLE CONTENT ORDER (EACH ROLE)
For EACH role, ensure bullets collectively cover:

1) Source systems (named explicitly).  
2) Ingestion and orchestration (single toolset; dev/stg/prod; secrets manager).  
3) Architecture (Medallion or equivalent; chosen warehouse; semantic/BI layer).  
4) Scale/Cost/Resilience  
   - At least one bullet on partitioning, clustering/Z-ORDER, compaction/OPTIMIZE, autoscaling, retries/backoff, backfills, cost caps.  
5) Real data logics (choose 1-2 per role, and NEVER duplicate the SAME logic in another company)  
   - Examples: SCD2, late-arriving data handling, idempotent MERGE/UPSERT, schema evolution, record-count checks between layers, null/dup thresholds, PII masking, row/column security, lineage, data contracts, SLA/SLO monitors.  
   - Do NOT repeat the SAME logic across companies: for example, if SCD2 is used at UnitedHealth Group (Senior), don’t claim SCD2 again at Meta or Amazon.  
6) CI/CD and testing  
   - dbt tests / Great Expectations / unit + integration tests; GitHub Actions / Azure DevOps / Jenkins; promotion gates, feature branching.  
7) Collaboration and outcomes  
   - Cross-functional work with data scientists, ML engineers, analysts, PMs, and business stakeholders; improved time-to-insight, reliability, or regulatory compliance.  
8) No repetition across companies  
   - Do NOT repeat the same exact pattern (for example, “built SCD2 for customer dimension”) for different employers.

---

BULLET WRITING RULES (strict)
1) Bullet count per role (MANDATORY):
    - UnitedHealth Group (Senior Data Engineer): 7 bullets
    - Meta: 6 bullets
    - Amazon: 6 bullets
    - UnitedHealth Group (SQL Data Engineer): 4 bullets
    - Tenet Healthcare: 4 bullets
2) Each bullet must:
    - Be a single string about between 200 to 250 characters inclusive (character count excludes the leading hyphen).
    - Describe ONE specific technical problem and the concrete solution using explicit core technology.
    - Reference at least three workflow stages among: Ingestion → Transformation → Modeling → Pipelines → Analytics/ML → Deployment/Monitoring.
    - Reference a realistic dataset or domain entity (e.g., claims, clickstream, transactions).
    - Use strong action verbs and show increasing ownership across roles.
    - Avoid duplicated phrasing or recycled sentence structures across bullets.
3) Bullets must reference technologies allowed for that role and the single selected cloud provider.
4) In Experience, Only 2-3 Bullets use metric as outcome(performance % improvement, latency reduction %) and at that moment, must use "about" for approximate explanation.

BULLET CONTENT AND STYLE
- Each bullet must explicitly reference:
    - (A) the core technical problem,
    - (B) the concrete solution with explicit core technology,
- Bullets should mention realistic enterprise constraints (SLA, batch/window, data privacy/compliance where relevant).
- CLOUD PLATFORM SINGLE SELECTION: choose exactly one cloud provider from the Job Description; apply it globally.
5. SKILL-EXPERIENCE MAPPING: every bullet must reference at least one skill from the Skills section.
6. EXPERIENCE REALISM & JD ALIGNMENT: final two roles (most recent) must align strongest to the Job Description.
7. BULLET CONTENT VALIDATION: each bullet must reference a dataset/domain entity, ≥3 workflow stages, core tech, and quantified outcome.

SELF-VALIDATION: before final output, internally validate:
        - bullet character counts (about 200 - 250),
        - skill category count (6-8),
        - skills per category (6-10),
        - single cloud platform usage,
        - mandatory categories present,
        - 75% skill usage coverage.
        If validation fails, regenerate until valid.
---

SKILLS
- JD skills first, then related skills and all required skills should be appeared in the experience bullets at least one time.
- Total: 30+ minimum
- Exactly 5-6 categories (strictly enforced)
- Categories: "Languages & Frameworks", "Data Engineering & Big Data", "Databases & Cloud Platforms", "Machine Learning & Advanced Analytics", "Analytics, BI & Data Governance"
- Format (no bullet points):
  **Category Name:** Skill1, Skill2, Skill3
- Only category label bold

---

EDUCATION
Master of Science (Data Science) | Eastern University | St. Davids, Pennsylvania | 2022
Master of Business Administration (Information Technology) | University of La Verne | La Verne, California | 2017
Bachelor of Science (Auto Engineering) | Jiangsu University | Zhenjiang, Jiangsu Province | 2014

---

CERTIFICATION
Amazon Redshift Development & Administration | Udemy |2017
Big Data with Apache Spark and Python | Udemy |2020
AWS Certified Cloud Practitioner | AWS |2019

---

HTML OUTPUT INSTRUCTIONS:
Complete valid HTML using the structure below.
No markdown bold (**) — use <strong> tags only.
LinkedIn URL must appear exactly as provided.
Put filename in <title>: Brian_Gou_CompanyName_JobTitle (spaces to underscores).
Do NOT include any external stylesheet <link> — CSS is injected by the host.
Do NOT wrap HTML in markdown fences.
Exception: <strong> tags ARE required for tech keywords (override the illegal-characters rule for those tags only).

<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>[replace with the filename]</title>
</head>
<body>
<div class="resume">
  <div class="header">
    <div class="name">Brian (Yi) Gou</div>
    <div class="title">Senior Data Engineer</div>
    <div class="contact">Tustin, California &nbsp;|&nbsp; briangou9@gmail.com &nbsp;|&nbsp; (929) 547 6426 &nbsp;|&nbsp; <a href="https://www.linkedin.com/in/brian-gou-5838541a7/">linkedin.com/in/brian-gou-5838541a7</a></div>
    <div class="header-dotted"></div>
  </div>
  <div class="section">
    <div class="section-title">Professional Summary</div>
    <p class="summary">Write summary here.</p>
  </div>
  <div class="section">
    <div class="section-title">Work Experience</div>
    <!-- Repeat this block for each of the five roles, in the order given. -->
    <div class="job">
      <div class="job-header">
        <span class="job-company">Company Name</span>
        <span class="job-date">Mon YYYY - Mon YYYY</span>
      </div>
      <div class="job-role">Job Title | City, State</div>
      <ul>
        <li>Bullet with <strong>tech keywords</strong> in strong tags.</li>
      </ul>
    </div>
  </div>
  <div class="section">
    <div class="section-title">Skills</div>
    <div class="skills">
      <p><strong>Languages &amp; Frameworks:</strong> <span>skills here</span></p>
    </div>
  </div>
  <div class="section">
    <div class="section-title">Education</div>
    <!-- Repeat this block for each of the three education rows, in the order given. -->
    <div class="edu">
      <div class="edu-row">
        <span class="edu-degree">Degree (Field)</span>
        <span class="edu-date">YYYY</span>
      </div>
      <div class="edu-school">University | City, State</div>
    </div>
  </div>
  <div class="section">
    <div class="section-title">Certification</div>
    <!-- Repeat this block for each of the three certifications, in the order given. -->
    <div class="edu">
      <div class="edu-row">
        <span class="edu-degree">Certification Name</span>
        <span class="edu-date">YYYY</span>
      </div>
      <div class="edu-school">Issuer</div>
    </div>
  </div>
</div>
</body>
</html>

After the HTML resume, write a short impactful cover letter as plain text only (no HTML).
