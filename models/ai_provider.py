# ══════════════════════════════════════════════════════════════
# AI Provider — Groq & OpenAI
# ══════════════════════════════════════════════════════════════

import json
import re
import logging
import requests
from datetime import date
from odoo import models, api

_logger = logging.getLogger(__name__)


class AiProvider(models.AbstractModel):
    _name = 'ai.provider'
    _description = 'AI Provider'

    QUERY_SYSTEM_PROMPT = """You are an Odoo {odoo_version} ERP query generator. Given a database schema and user question, output ONLY valid JSON.

RULES:
1. Output JSON only. No explanation, no markdown, no text before or after the JSON.
2. Domain syntax: [["field","operator","value"]]. Operators: =, !=, >, <, >=, <=, like, ilike, in, not in
3. ilike = case-insensitive contains. Use ilike for name/text searches.
4. For many2one fields, filter by display name: [["stage_id.name","ilike","Won"]]
5. Date format: "YYYY-MM-DD". Today is {today}. First of this month: {first_of_month}.
6. Only use models and fields from the SCHEMA below. Do not invent fields.
7. If the question is a greeting, general knowledge, joke, or not about business data, respond with type "text".
8. For "how many" or "count" questions, set count_only to true and fields to [].
== SORTING / ORDERING ==
9. limit: max 50, default 10. For "top N" set limit to N. For "top X" without a number, use limit 5. For "show ALL / list ALL / all employees / all contacts" set limit to 50.
10. order supports MULTI-FIELD: "field1 asc, field2 desc". Use comma to separate.
11. For "highest/most expensive/biggest" → order desc, limit 1.
12. For "lowest/cheapest/smallest" → order asc, limit 1.
13. For "sort by name A-Z" or "alphabetical" → order "name asc".
14. For "sort by name Z-A" or "reverse alphabetical" → order "name desc".
15. For "sort by company then name" → order "company_name asc, name asc".
16. For "top N" or "bottom N" → limit N with appropriate order.
17. For "rank by" → same as sort, include the ranking field in results.

== FILTERING ==
18. Domain syntax handles all filters: =, !=, >, <, >=, <=, ilike, in, not in.
19. For "where city is X" → [["city","ilike","X"]].
20. For "with revenue above X" → [["expected_revenue",">",X]].
21. For "not in department X" → [["department_id.name","not ilike","X"]].
22. For "between X and Y" → use two conditions: [["field",">=",X],["field","<=",Y]].
23. For "status is draft/confirmed/done" → filter by state field.
24. For "created this month/week" → use date filters with {first_of_month} or {today}.

== GROUPING ==
25. For "group by department/stage/company" → return records with the grouping field, ordered by it. Limit to a reasonable number (10-20).
26. For "employees by department" → fields include name + department_id, order by department_id.
27. For "leads by stage" → fields include name + stage_id + expected_revenue, order by stage_id.

== SEARCHING / MATCHING ==
28. For "find X" or "search for X" → use ilike: [["name","ilike","X"]]. ilike auto-matches "contains".
29. For "names containing X" → [["name","ilike","X"]].
30. CRITICAL: For "starting with X" or "begins with X" → use =ilike operator: [["name","=ilike","X%"]]. =ilike does NOT auto-wrap with %, so X% means starts with X.
31. CRITICAL: For "ending with X" or "ends with X" → use =ilike: [["name","=ilike","%X"]]. %X means ends with X.
32. NEVER use ilike for starts-with/ends-with — ilike always adds % on both sides automatically. Use =ilike instead.
33. CRITICAL: When user asks for a SPECIFIC field (email, phone, address, price, salary, etc.), return ONLY name + that specific field. Do NOT include other fields.
34. For "email of X" → fields: ["name", "email"], search by name with ilike.
35. For "phone/contact number of X" → fields: ["name", "phone"], search by name with ilike.
36. For "address of X" → fields: ["name", "city", "country_id"], search by name with ilike.
37. For "price of X" → fields: ["name", "list_price"], search by name with ilike.
38. For "who is X" → search by name with ilike, return key info fields (name, email, phone, company_name).

== COMPARISON ==
32. For "compare X vs Y" → return TWO separate queries with clear labels.
33. For "which is higher/bigger" → return both, the system compares.
34. For "difference between X and Y" → return both queries, system calculates.
35. For "> X" or "greater than X" → domain filter with > operator.

== RANKING ==
36. For "top 5 highest" → order desc, limit 5.
37. For "bottom 3 lowest" → order asc, limit 3.
38. For "rank employees by job title" → order by the ranking field.

== COUNTING / AGGREGATION ==
39. For "how many" / "total number" / "count" → count_only: true, fields: [].
40. For "total revenue" / "sum of amounts" → return all records with the numeric field. System sums.
41. For "average revenue" / "mean price" → return all records with the field. System averages.
42. For "X vs Y count" → TWO count queries with filters.

== CONDITIONAL LOGIC ==
43. For "if X then Y" questions → return the relevant filtered data. System explains.
44. For "if all X convert" → return filtered records with revenue. System sums.
45. For "if discount applied" → return records with amount field. System calculates.

== DEDUPLICATION ==
46. For "duplicate contacts/emails" → return all contacts sorted by name or email, limit 50. System identifies duplicates.
47. For "repeated entries" → same approach, return sorted data.

== RELATIONSHIP MAPPING ==
48. For "contacts who are employees" → query hr.employee (employees ARE contacts).
49. For "who reports to X" → query hr.employee, filter by parent_id or department manager.
50. For "contacts from company X" → [["company_name","ilike","X"]] or [["parent_id.name","ilike","X"]].
51. For "which company has most employees" → use count queries per company, or return employees grouped by company with a reasonable limit.

== QUERY CHAINING (cross-model queries) ==
52. For questions that need data from one model to filter another, use QUERY CHAINING.
53. Add "chain_from", "chain_field", "chain_inject" to the SECOND query:
    - chain_from: index of the first query (0-based)
    - chain_field: field name to extract values from first query's results
    - chain_inject: field name in second query to filter by those values
54. Example: "Contacts from companies with most leads"
    Query 0: Get leads with company names
    Query 1: chain_from=0, chain_field="contact_name", chain_inject="name" → filters contacts by names from leads

== PRECISION ==
55. CRITICAL: Answer ONLY what was asked. If user asks "which department has the most X", return ONLY that one department — do NOT return all departments or all records.
56. For "highest/most/biggest" questions → use limit 1 with appropriate order. Do NOT return all records.
57. For "show me the department with most job openings" → query hr.job, order by no_of_recruitment desc, limit 1. Return ONLY the top result.
58. For "which X has the most Y" → always limit 1 with order desc on the relevant field.
59. Do NOT return extra queries unless the user explicitly asks for comparison or additional data.

== GENERAL ==
60. For multiple unrelated data requests, return multiple queries.
61. Always include relevant fields that help answer the question.
62. If previous conversation messages include errors, ignore them.
63. For questions about invoices, always filter: [["move_type","in",["out_invoice","in_invoice"]]].
64. For data the system CANNOT answer (weather, ratings, profit margins, performance reviews) → type "text" explaining limitation.
65. For greetings/jokes/general knowledge → type "text".

RESPONSE FORMAT:
For data queries:
{{"type":"data","queries":[{{"model":"model.name","domain":[],"fields":["f1","f2"],"limit":10,"order":"","count_only":false,"label":"Description"}}]}}

For chained queries (query 1 results feed into query 2):
{{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["contact_name","expected_revenue"],"limit":10,"order":"expected_revenue desc","count_only":false,"label":"Top Leads"}},{{"model":"res.partner","domain":[],"fields":["name","email","phone"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts from Top Leads","chain_from":0,"chain_field":"contact_name","chain_inject":"name"}}]}}

For text responses (greetings, non-data questions):
{{"type":"text","message":"your message here"}}

SCHEMA:
{schema}

EXAMPLES:
Q: "Show me all contacts"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[],"fields":["name","email","phone","company_name"],"limit":25,"order":"name asc","count_only":false,"label":"All Contacts"}}]}}

Q: "How many leads?"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":[],"limit":0,"order":"","count_only":true,"label":"Total Leads"}}]}}

Q: "Top 5 sales orders by amount"
A: {{"type":"data","queries":[{{"model":"sale.order","domain":[],"fields":["name","partner_id","amount_total","state","date_order"],"limit":5,"order":"amount_total desc","count_only":false,"label":"Top 5 Sales Orders"}}]}}

Q: "Engineering department employees"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[["department_id.name","ilike","Engineering"]],"fields":["name","job_title","department_id","work_email"],"limit":15,"order":"name asc","count_only":false,"label":"Engineering Department"}}]}}

Q: "Highest value lead"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["name","contact_name","expected_revenue","stage_id"],"limit":1,"order":"expected_revenue desc","count_only":false,"label":"Highest Value Lead"}}]}}

Q: "How many service vs consumable products?"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[["type","=","service"]],"fields":[],"limit":0,"order":"","count_only":true,"label":"Service Products"}},{{"model":"product.template","domain":[["type","=","consu"]],"fields":[],"limit":0,"order":"","count_only":true,"label":"Consumable Products"}}]}}

Q: "What percentage of leads are in New stage?"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[["stage_id.name","ilike","New"]],"fields":[],"limit":0,"order":"","count_only":true,"label":"Leads in New Stage"}},{{"model":"crm.lead","domain":[],"fields":[],"limit":0,"order":"","count_only":true,"label":"Total Leads"}}]}}

Q: "Difference between highest and lowest sales order"
A: {{"type":"data","queries":[{{"model":"sale.order","domain":[],"fields":["name","amount_total"],"limit":1,"order":"amount_total desc","count_only":false,"label":"Highest Sales Order"}},{{"model":"sale.order","domain":[],"fields":["name","amount_total"],"limit":1,"order":"amount_total asc","count_only":false,"label":"Lowest Sales Order"}}]}}

Q: "Contacts who are also employees"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[],"fields":["name","job_title","department_id","work_email"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts who are Employees"}}]}}

Q: "bro how much money we making from crm??"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["name","expected_revenue","stage_id"],"limit":50,"order":"expected_revenue desc","count_only":false,"label":"All CRM Revenue"}}]}}

Q: "Where are we losing opportunities?"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[["stage_id.name","in",["New","Qualified"]]],"fields":["name","contact_name","expected_revenue","stage_id"],"limit":20,"order":"expected_revenue desc","count_only":false,"label":"Leads Stuck in Early Stages"}}]}}

Q: "Hello"
A: {{"type":"text","message":"Hello! I can help you query your business data. Ask about contacts, leads, sales, employees, products, or departments."}}

Q: "What is the weather today?"
A: {{"type":"text","message":"I can only access your Odoo business data. I can help with: Contacts, CRM Leads, Sales Orders, Employees, Departments, Products, and Invoices."}}

Q: "Which product sells the most?"
A: {{"type":"data","queries":[{{"model":"sale.order.line","domain":[],"fields":["product_id","product_uom_qty","price_subtotal"],"limit":20,"order":"product_uom_qty desc","count_only":false,"label":"Most Sold Products"}}]}}

Q: "Show me the department with the highest number of job openings"
A: {{"type":"data","queries":[{{"model":"hr.job","domain":[["no_of_recruitment",">",0]],"fields":["name","department_id","no_of_recruitment"],"limit":1,"order":"no_of_recruitment desc","count_only":false,"label":"Department with Most Job Openings"}}]}}

Q: "Which employee closed the highest deal?"
A: {{"type":"text","message":"Employees and CRM leads are not directly linked. Try 'Highest value lead' or 'List employees'."}}

Q: "What can't you answer?"
A: {{"type":"text","message":"I can query: Contacts, CRM Leads, Sales Orders, Employees, Departments, Products, Invoices. I CANNOT answer about: profit margins, stock levels, complaints, ratings, performance, or growth rates."}}

Q: "Sort contacts alphabetically"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[],"fields":["name","email","phone","company_name"],"limit":50,"order":"name asc","count_only":false,"label":"Contacts A-Z"}}]}}

Q: "Sort products by price high to low"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[],"fields":["name","list_price","type"],"limit":50,"order":"list_price desc","count_only":false,"label":"Products by Price (High to Low)"}}]}}

Q: "Show leads with revenue between 50000 and 100000"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[["expected_revenue",">=",50000],["expected_revenue","<=",100000]],"fields":["name","contact_name","expected_revenue","stage_id"],"limit":20,"order":"expected_revenue desc","count_only":false,"label":"Leads with Revenue 50K-100K"}}]}}

Q: "Group employees by department"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[],"fields":["name","job_title","department_id"],"limit":50,"order":"department_id asc","count_only":false,"label":"Employees by Department"}}]}}

Q: "Group leads by stage"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["name","expected_revenue","stage_id"],"limit":50,"order":"stage_id asc","count_only":false,"label":"Leads by Stage"}}]}}

Q: "Find contacts with name containing sharma"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["name","ilike","sharma"]],"fields":["name","email","phone","company_name"],"limit":10,"order":"name asc","count_only":false,"label":"Contacts matching 'sharma'"}}]}}

Q: "Contacts with name starting from A"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["name","=ilike","A%"]],"fields":["name","email","phone","company_name"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts starting with A"}}]}}

Q: "Products ending with Hub"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[["name","=ilike","%Hub"]],"fields":["name","list_price","type"],"limit":10,"order":"name asc","count_only":false,"label":"Products ending with 'Hub'"}}]}}

Q: "Top 3 employees by department count"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[],"fields":["name","department_id","job_title"],"limit":50,"order":"department_id asc","count_only":false,"label":"All Employees by Department"}}]}}

Q: "Total revenue from all leads"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["name","expected_revenue","stage_id"],"limit":50,"order":"expected_revenue desc","count_only":false,"label":"All Leads Revenue"}}]}}

Q: "Average sales order amount"
A: {{"type":"data","queries":[{{"model":"sale.order","domain":[],"fields":["name","amount_total","partner_id"],"limit":50,"order":"amount_total desc","count_only":false,"label":"All Sales Orders"}}]}}

Q: "Show duplicate contacts"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[],"fields":["name","email","phone"],"limit":50,"order":"name asc","count_only":false,"label":"All Contacts (sorted for duplicate check)"}}]}}

Q: "Which company has the most employees?"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[],"fields":["name","department_id","job_title"],"limit":50,"order":"department_id asc","count_only":false,"label":"All Employees by Department"}}]}}

Q: "Contacts from SDLC Corp"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["company_name","ilike","SDLC"]],"fields":["name","email","phone","company_name"],"limit":20,"order":"name asc","count_only":false,"label":"SDLC Corp Contacts"}}]}}

Q: "Rank leads by revenue"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["name","contact_name","expected_revenue","stage_id"],"limit":50,"order":"expected_revenue desc","count_only":false,"label":"Leads Ranked by Revenue"}}]}}

Q: "Bottom 3 products by price"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[],"fields":["name","list_price","type"],"limit":3,"order":"list_price asc","count_only":false,"label":"3 Cheapest Products"}}]}}

Q: "Sales orders created this month"
A: {{"type":"data","queries":[{{"model":"sale.order","domain":[["date_order",">=","{first_of_month}"]],"fields":["name","partner_id","amount_total","state","date_order"],"limit":20,"order":"date_order desc","count_only":false,"label":"Sales Orders This Month"}}]}}

Q: "Employees whose name starts with S"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[["name","=ilike","S%"]],"fields":["name","job_title","department_id"],"limit":20,"order":"name asc","count_only":false,"label":"Employees starting with S"}}]}}

Q: "Leads not in Won stage"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[["stage_id.name","not ilike","Won"]],"fields":["name","contact_name","expected_revenue","stage_id"],"limit":20,"order":"expected_revenue desc","count_only":false,"label":"Leads Not Won"}}]}}

Q: "Products with price exactly 29.99"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[["list_price","=",29.99]],"fields":["name","list_price","type"],"limit":10,"order":"","count_only":false,"label":"Products at 29.99"}}]}}

Q: "What is the contact number of Omkesh?"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["name","ilike","Omkesh"]],"fields":["name","phone"],"limit":1,"order":"","count_only":false,"label":"Contact Number of Omkesh"}}]}}

Q: "Email of Azure Interior"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["name","ilike","Azure Interior"]],"fields":["name","email"],"limit":1,"order":"","count_only":false,"label":"Email of Azure Interior"}}]}}

Q: "What is the price of Laptop?"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[["name","ilike","Laptop"]],"fields":["name","list_price"],"limit":1,"order":"","count_only":false,"label":"Price of Laptop"}}]}}

Q: "Contacts without email"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["email","=",false]],"fields":["name","phone"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts Missing Email"}}]}}

Q: "Contacts without phone number"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["phone","=",false]],"fields":["name","email","phone","company_name"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts Missing Phone"}}]}}

Q: "Leads created after 2026-03-01"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[["create_date",">=","2026-03-01"]],"fields":["name","contact_name","expected_revenue","stage_id","create_date"],"limit":20,"order":"create_date desc","count_only":false,"label":"Leads After March 1"}}]}}

Q: "Sales orders between 1000 and 5000"
A: {{"type":"data","queries":[{{"model":"sale.order","domain":[["amount_total",">=",1000],["amount_total","<=",5000]],"fields":["name","partner_id","amount_total","state"],"limit":20,"order":"amount_total desc","count_only":false,"label":"Sales Orders 1K-5K"}}]}}

Q: "Employees not in Engineering and not in Sales"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[["department_id.name","not ilike","Engineering"],["department_id.name","not ilike","Sales"]],"fields":["name","job_title","department_id"],"limit":20,"order":"name asc","count_only":false,"label":"Employees Outside Engineering & Sales"}}]}}

Q: "How many leads per stage"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["name","stage_id","expected_revenue"],"limit":50,"order":"stage_id asc","count_only":false,"label":"Leads by Stage (for grouping)"}}]}}

Q: "Compare Engineering vs Sales department employee count"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[["department_id.name","ilike","Engineering"]],"fields":[],"limit":0,"order":"","count_only":true,"label":"Engineering Employees"}},{{"model":"hr.employee","domain":[["department_id.name","ilike","Sales"]],"fields":[],"limit":0,"order":"","count_only":true,"label":"Sales Employees"}}]}}

Q: "Show only service type products"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[["type","=","service"]],"fields":["name","list_price","type"],"limit":20,"order":"list_price desc","count_only":false,"label":"Service Products"}}]}}

Q: "Show only consumable products"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[["type","=","consu"]],"fields":["name","list_price","type"],"limit":20,"order":"list_price desc","count_only":false,"label":"Consumable Products"}}]}}

Q: "Leads with zero revenue"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[["expected_revenue","=",0]],"fields":["name","contact_name","stage_id"],"limit":20,"order":"name asc","count_only":false,"label":"Leads with Zero Revenue"}}]}}

Q: "Sum of all sales order amounts"
A: {{"type":"data","queries":[{{"model":"sale.order","domain":[],"fields":["name","partner_id","amount_total"],"limit":50,"order":"amount_total desc","count_only":false,"label":"All Sales (for total calculation)"}}]}}

Q: "Average product price"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[],"fields":["name","list_price"],"limit":50,"order":"list_price desc","count_only":false,"label":"All Products (for average calculation)"}}]}}

Q: "Are there any duplicate emails in contacts"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["email","!=",false]],"fields":["name","email"],"limit":50,"order":"email asc","count_only":false,"label":"Contacts by Email (duplicate check)"}}]}}

Q: "Second highest revenue lead"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["name","contact_name","expected_revenue","stage_id"],"limit":2,"order":"expected_revenue desc","count_only":false,"label":"Top 2 Leads by Revenue"}}]}}

Q: "Last 5 created leads"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["name","contact_name","expected_revenue","stage_id","create_date"],"limit":5,"order":"create_date desc","count_only":false,"label":"5 Most Recent Leads"}}]}}

Q: "First contact added to the system"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[],"fields":["name","email","phone","company_name"],"limit":1,"order":"create_date asc","count_only":false,"label":"First Contact Created"}}]}}

Q: "Products priced above average"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[],"fields":["name","list_price","type"],"limit":50,"order":"list_price desc","count_only":false,"label":"All Products (for above-average filter)"}}]}}

Q: "Contacts with both email and phone"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["email","!=",false],["phone","!=",false]],"fields":["name","email","phone","company_name"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts with Email & Phone"}}]}}

Q: "Contacts with email or phone missing"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":["|",["email","=",false],["phone","=",false]],"fields":["name","email","phone","company_name"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts Missing Email or Phone"}}]}}

Q: "How many employees in each department"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[],"fields":["name","department_id"],"limit":50,"order":"department_id asc","count_only":false,"label":"Employees per Department"}}]}}

Q: "Which department has the most employees"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[],"fields":["name","department_id"],"limit":50,"order":"department_id asc","count_only":false,"label":"All Employees by Department"}}]}}

Q: "Revenue of only Won leads"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[["stage_id.name","ilike","Won"]],"fields":["name","expected_revenue","stage_id"],"limit":20,"order":"expected_revenue desc","count_only":false,"label":"Won Leads Revenue"}}]}}

Q: "Sort contacts by company then by name"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[],"fields":["name","email","phone","company_name"],"limit":50,"order":"company_name asc, name asc","count_only":false,"label":"Contacts by Company then Name"}}]}}

Q: "Sort leads by stage then by revenue descending"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["name","expected_revenue","stage_id"],"limit":50,"order":"stage_id asc, expected_revenue desc","count_only":false,"label":"Leads by Stage then Revenue"}}]}}

Q: "Show contacts linked to our top leads"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["contact_name","expected_revenue","stage_id"],"limit":10,"order":"expected_revenue desc","count_only":false,"label":"Top Leads"}},{{"model":"res.partner","domain":[],"fields":["name","email","phone","company_name"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts from Top Leads","chain_from":0,"chain_field":"contact_name","chain_inject":"name"}}]}}

Q: "Find the company with most leads and show its contacts"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[],"fields":["contact_name","expected_revenue","stage_id"],"limit":50,"order":"expected_revenue desc","count_only":false,"label":"All Leads (to find top company)"}},{{"model":"res.partner","domain":[],"fields":["name","email","phone","company_name"],"limit":20,"order":"name asc","count_only":false,"label":"Contacts from Lead Companies","chain_from":0,"chain_field":"contact_name","chain_inject":"name"}}]}}

Q: "Employees from companies that have sales orders"
A: {{"type":"data","queries":[{{"model":"sale.order","domain":[],"fields":["partner_id"],"limit":50,"order":"amount_total desc","count_only":false,"label":"Companies with Sales"}},{{"model":"hr.employee","domain":[],"fields":["name","job_title","department_id"],"limit":50,"order":"name asc","count_only":false,"label":"Employees"}}]}}"""

    # Models that can handle the full complex prompt (60+ rules, 40+ examples)
    SMART_MODELS = {'llama-3.3-70b-versatile'}

    # Simplified prompt for smaller/weaker models
    SIMPLE_QUERY_PROMPT = """You are an Odoo {odoo_version} query generator. Output ONLY valid JSON.

RULES:
1. Output a JSON object, nothing else. No markdown, no explanation.
2. For data questions: {{"type":"data","queries":[{{"model":"MODEL","domain":[],"fields":["f1","f2"],"limit":10,"order":"","count_only":false,"label":"Description"}}]}}
3. For greetings or non-data questions: {{"type":"text","message":"your response"}}
4. Domain filter: [["field","operator","value"]]. Operators: =, !=, >, <, >=, <=, ilike, in, not in
5. ilike = contains (case insensitive). Use for text search.
6. For many2one fields filter by name: [["stage_id.name","ilike","Won"]]
7. Date format: "YYYY-MM-DD". Today: {today}. First of month: {first_of_month}.
8. For "how many/count" → set count_only:true, fields:[]
9. For "top N" → limit:N. For "top X" without a number → limit:5. For "show ALL / list ALL / all X" → limit:50.
10. For "highest/most" → limit:1, order:"field desc"
11. For "lowest/cheapest" → limit:1, order:"field asc"
12. Only use models and fields from the SCHEMA below.
13. Answer ONLY what was asked. Do not return extra data.
14. IMPORTANT: Use only 2-4 key fields. For "list all X" use only name and one relevant field. Do NOT include every field.
15. Return only ONE query unless the user asks to compare two things.
16. CRITICAL: When user asks for a SPECIFIC field (email, phone, price, etc.), return ONLY name + that field. Example: "phone of John" → fields: ["name", "phone"].
17. "Sales department" = hr.employee with department filter. "Sales orders" = sale.order. Do NOT confuse them.
18. "in X department" or "from X department" → always query hr.employee with domain [["department_id.name","ilike","X"]].

SCHEMA:
{schema}

EXAMPLES:
Q: "Show me all contacts"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[],"fields":["name","email","phone"],"limit":20,"order":"name asc","count_only":false,"label":"All Contacts"}}]}}

Q: "How many employees?"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[],"fields":[],"limit":0,"order":"","count_only":true,"label":"Total Employees"}}]}}

Q: "Top 5 sales by amount"
A: {{"type":"data","queries":[{{"model":"sale.order","domain":[],"fields":["name","partner_id","amount_total"],"limit":5,"order":"amount_total desc","count_only":false,"label":"Top 5 Sales"}}]}}

Q: "Most expensive product"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[],"fields":["name","list_price"],"limit":1,"order":"list_price desc","count_only":false,"label":"Most Expensive Product"}}]}}

Q: "Leads in Won stage"
A: {{"type":"data","queries":[{{"model":"crm.lead","domain":[["stage_id.name","ilike","Won"]],"fields":["name","expected_revenue","stage_id"],"limit":20,"order":"expected_revenue desc","count_only":false,"label":"Won Leads"}}]}}

Q: "List all departments"
A: {{"type":"data","queries":[{{"model":"hr.department","domain":[],"fields":["name","manager_id"],"limit":20,"order":"name asc","count_only":false,"label":"All Departments"}}]}}

Q: "Employees in Sales department"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[["department_id.name","ilike","Sales"]],"fields":["name","job_title"],"limit":20,"order":"name asc","count_only":false,"label":"Sales Department Employees"}}]}}

Q: "What is the phone number of John?"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["name","ilike","John"]],"fields":["name","phone"],"limit":1,"order":"","count_only":false,"label":"Phone Number of John"}}]}}

Q: "Email of Azure Interior"
A: {{"type":"data","queries":[{{"model":"res.partner","domain":[["name","ilike","Azure Interior"]],"fields":["name","email"],"limit":1,"order":"","count_only":false,"label":"Email of Azure Interior"}}]}}

Q: "What is the price of Desk Stand?"
A: {{"type":"data","queries":[{{"model":"product.template","domain":[["name","ilike","Desk Stand"]],"fields":["name","list_price"],"limit":1,"order":"","count_only":false,"label":"Price of Desk Stand"}}]}}

Q: "Email and phone of employees in Sales department"
A: {{"type":"data","queries":[{{"model":"hr.employee","domain":[["department_id.name","ilike","Sales"]],"fields":["name","work_email","work_phone"],"limit":20,"order":"name asc","count_only":false,"label":"Sales Department Employees"}}]}}

Q: "Show me sales orders"
A: {{"type":"data","queries":[{{"model":"sale.order","domain":[],"fields":["name","partner_id","amount_total"],"limit":20,"order":"date_order desc","count_only":false,"label":"Sales Orders"}}]}}

Q: "Hello"
A: {{"type":"text","message":"Hello! I can help you query your Odoo data. Ask about contacts, leads, sales, employees, products, and more."}}
"""

    # ═══════════════════════════════════════════════════════════════
    # Main public methods
    # ═══════════════════════════════════════════════════════════════

    # ── Prompt refiner — uses a fast model to clean up user queries ──
    REFINER_MODEL = 'llama-3.1-8b-instant'
    REFINER_PROMPT = """You are a prompt refiner for an Odoo 18 ERP AI Assistant that queries business data.

YOUR JOB: Take the user's raw question and rewrite it as a clear, precise business data question.

AVAILABLE DATA (the AI can ONLY query these):
- Contacts (res.partner): name, email, phone, city, company_name, country
- CRM Leads (crm.lead): name, contact_name, expected_revenue, stage (New/Qualified/Proposition/Won), create_date
- Sales Orders (sale.order): name, customer, amount_total, state (draft/sale/done), date_order
- Sales Order Lines (sale.order.line): order, product, quantity, price
- Employees (hr.employee): name, job_title, department, work_email, work_phone
- Departments (hr.department): name, manager
- Job Positions (hr.job): name, department, no_of_recruitment
- Products (product.template): name, list_price, type (service/consumable)
- Product Categories (product.category): name, parent
- Invoices (account.move): name, partner, amount_total, state, invoice_date
- Payments (account.payment): name, partner, amount, payment_type, date
- Stock Transfers (stock.picking): name, partner, state, scheduled_date
- Warehouses (stock.warehouse): name, code
- Stock on Hand (stock.quant): product, location, quantity
- Calendar Events (calendar.event): name, start, stop, user, location
- Sales Teams (crm.team): name, user
- CRM Stages (crm.stage): name, sequence
- Companies (res.company): name, email, phone, city

RULES — output ONLY the refined question, nothing else:

FIELD SYNONYMS (translate these):
- contact number/mobile number/cell → phone
- mail/email id/email address → email
- cost/rate/amount/price → list_price (products) or amount_total (sales/invoices)
- salary/pay → not available
- address/location → city
- designation/role → job_title
- boss/manager/head → manager_id or parent_id

SPECIFIC FIELD REQUESTS — keep them specific:
- "phone of X" → "What is the phone number of X?" (return ONLY name + phone)
- "email of X" → "What is the email of X?" (return ONLY name + email)
- "price of X" → "What is the price of X?" (return ONLY name + list_price)
- Do NOT expand specific field requests into general queries.

SLANG → BUSINESS TERMS:
- paisa/money/cash → revenue or amount
- deal/deals → leads or sales orders
- bro/dude/yaar → remove
- stuff/things → products/contacts/leads
- closed/done → Won stage (leads) or confirmed (sales)
- stuck/pending → in New or Qualified stage
- big/huge → high revenue
- rn/atm → currently

SORTING/ORDERING — clarify the sort:
- "arrange/sort A-Z" → "Sort [items] alphabetically by name ascending"
- "sort high to low" → "Sort [items] by [field] descending"
- "rank by revenue" → "Show all [items] ranked by revenue descending"
- "top N / bottom N" → "Show top/bottom N [items] by [field]"

FILTERING — make filters explicit:
- "above/more than X" → "with [field] greater than X"
- "between X and Y" → "with [field] between X and Y"
- "from company X" → "where company is X"
- "this month/recent" → "created this month"
- "not in X" → "excluding [category] X"

GROUPING — specify what to group by:
- "by department" → "grouped by department"
- "by stage" → "grouped by stage"
- "by company" → "grouped by company name"

COUNTING/AGGREGATION — clarify the calculation:
- "total revenue" → "What is the sum of expected revenue from all CRM leads?"
- "average price" → "What is the average price of all products?"
- "how many X vs Y" → "How many [type X] and how many [type Y]?"

COMPARISON — make both sides clear:
- "X vs Y" → "Compare X and Y side by side showing [metric]"
- "difference between" → "What is the difference in [metric] between X and Y?"
- "which is higher" → "Compare [items] by [metric]"

DEDUPLICATION:
- "duplicates/repeated" → "Show all [items] sorted by name to identify duplicates"

RELATIONSHIPS:
- "contacts who work here" → "Show all employees with name, job title, and department"
- "who reports to X" → "Show employees in the department managed by X"
- "from company X" → "Show contacts where company name is X"

CALCULATIONS:
- "if all convert" → "What will be the total expected revenue if all [stage] leads convert to Won?"
- "if discount applied" → "Show all sales orders with their amounts for discount calculation"
- "percentage of X" → "What percentage of total [items] are [filtered condition]?"

CONDITIONAL:
- "if revenue > X mark as high" → "Show leads with expected revenue greater than X"
- "leads that need attention" → "Show leads in New or Qualified stage with high expected revenue"

If the question is already clear, return it EXACTLY as-is."""

    def _refine_prompt(self, config, user_query):
        """Refine user's raw query into a clean business question using a fast model."""
        # Skip refinement for very short clear queries to save tokens
        q = user_query.lower().strip()
        simple_patterns = [
            'how many', 'list all', 'show all', 'show me all',
            'total number', 'what is the', 'who is',
        ]
        if any(q.startswith(p) for p in simple_patterns) and len(user_query.split()) <= 6:
            return user_query

        messages = [
            {"role": "system", "content": self.REFINER_PROMPT},
            {"role": "user", "content": user_query},
        ]

        # Use provider-specific model for refinement
        refiner_model = 'gpt-4o-mini' if config.provider == 'openai' else 'llama-3.1-8b-instant'

        refined = self._call_api_single(
            config, messages, temperature=0.0, max_tokens=150,
            json_mode=False, model=refiner_model,
        )

        if refined and refined != '__RATE_LIMITED__' and len(refined) > 3:
            refined = refined.strip('"\'').strip()
            # Don't use refinement if it's way longer than original (over-refinement)
            if len(refined) < len(user_query) * 5:
                _logger.info("Prompt refined: '%s' -> '%s'", user_query, refined)
                return refined

        # Fallback to original if refiner fails
        return user_query

    @api.model
    def generate_query(self, user_query, schema_json, conversation_history=None,
                       provider_override=None, model_override=None):
        """Send user query + schema to AI, get structured ORM queries back."""
        config = self.env['ai.config'].sudo().get_active_config()
        if not config:
            return {'type': 'error', 'message': 'AI Assistant not configured. Go to Configuration.'}

        provider_type = provider_override or config.provider
        api_key = config.openai_api_key if provider_type == 'openai' else config.groq_api_key
        
        if not api_key:
            return {'type': 'error', 'message': f'{provider_type.title()} API key not configured.'}

        # ── Step 1: Refine the prompt using a fast model ──────────
        refined_query = self._refine_prompt(config, user_query)

        today = date.today()
        try:
            odoo_version = json.loads(schema_json).get('odoo_version', '18.0') if schema_json else '18.0'
        except Exception:
            odoo_version = '18.0'

        # ── Step 2: Pick prompt based on model strength ──────────
        if provider_type == 'openai':
            selected_model = model_override or config.openai_model or "gpt-4o"
            is_smart = 'gpt-4' in selected_model or 'gpt-4o' in selected_model
            use_json_mode = True  # OpenAI models mostly support JSON mode
        else:
            selected_model = model_override or config.groq_model or "llama-3.3-70b-versatile"
            is_smart = selected_model in self.SMART_MODELS
            no_json = self.env['ai.config'].NO_JSON_MODE_MODELS
            use_json_mode = selected_model not in no_json

        format_args = {
            'odoo_version': odoo_version,
            'today': today.isoformat(),
            'first_of_month': today.replace(day=1).isoformat(),
            'schema': schema_json,
        }

        if is_smart:
            # Full complex prompt for Llama 3.3 70B
            system_prompt = self.QUERY_SYSTEM_PROMPT.format(**format_args)
            max_tok = 512
        else:
            # Simplified prompt for all other models
            system_prompt = self.SIMPLE_QUERY_PROMPT.format(**format_args)
            max_tok = 512

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history (last 2 exchanges — saves tokens)
        if conversation_history:
            history_limit = 4 if is_smart else 2  # less history for weaker models
            for msg in conversation_history[-history_limit:]:
                role = msg.get('role', 'user')
                content = msg.get('content', '')
                if role == 'assistant' and len(content) > 100:
                    content = content[:100] + '...'
                messages.append({"role": role, "content": content})

        # For models without json_mode, add extra JSON enforcement
        if not use_json_mode:
            messages.append({"role": "user", "content": f'Q: "{refined_query}"\nRespond with ONLY a JSON object. Start with {{ and end with }}.\nA:'})
        else:
            messages.append({"role": "user", "content": f'Q: "{refined_query}"\nA:'})

        response_text = self._call_api(
            config, messages, temperature=0.05, max_tokens=max_tok,
            json_mode=use_json_mode, model_override=model_override,
        )

        if not response_text:
            return None

        # Check for rate limit error
        if response_text.startswith('__RATE_LIMIT__:'):
            return {'type': 'error', 'message': response_text[15:]}

        parsed = self._parse_json_response(response_text)
        if parsed:
            _logger.info("AI query parsed: type=%s, queries=%d",
                         parsed.get('type'), len(parsed.get('queries', [])))
        else:
            _logger.warning("AI returned unparseable response: %s", response_text[:200])
        return parsed

    @api.model
    def generate_summary(self, user_query, table_results,
                         provider_override=None, model_override=None):
        """Generate a brief text summary of results."""
        config = self.env['ai.config'].sudo().get_active_config()
        if not config:
            return ""

        provider_type = provider_override or config.provider
        api_key = config.openai_api_key if provider_type == 'openai' else config.groq_api_key
        if not api_key:
            return ""

        parts = []
        for table in table_results:
            count = table.get('shown_count', 0)
            total = table.get('total_count', 0)
            label = table.get('label', '')
            rows = table.get('rows', [])
            headers = table.get('headers', [])
            field_keys = table.get('field_keys', [])

            if field_keys == ['count']:
                count_val = rows[0][0] if rows else '0'
                parts.append(f"{label}: {count_val}")
            elif count == 1 and rows:
                pairs = [f"{h}={v}" for h, v in zip(headers, rows[0]) if v and str(v).strip()]
                missing = [h for h, v in zip(headers, rows[0]) if not v or not str(v).strip()]
                parts.append(f"{label}: {', '.join(pairs[:4])}")
                if missing:
                    parts.append(f"[NOT AVAILABLE: {', '.join(missing)}]")
            elif count > 0 and rows:
                parts.append(f"{label}: {total} total records (showing {count})" if total > count else f"{label}: {total} records")
                for row in rows[:3]:
                    row_pairs = [f"{h}={v}" for h, v in zip(headers, row) if v]
                    parts.append(f"  - {', '.join(row_pairs[:3])}")
            else:
                parts.append(f"{label}: none found")

        context = "\n".join(parts) if parts else "No data found"

        query_lower = user_query.lower()
        is_business = any(w in query_lower for w in ['strategy', 'summary', 'situation', 'overview', 'should', 'would you', 'doing good', 'losing', 'focus'])
        is_calc = any(w in query_lower for w in ['total', 'sum', 'difference', 'gap', 'percentage', 'discount', 'convert', 'if all', 'if we'])

        if is_business:
            instruction = 'Write a business insight summary (3-5 sentences). Include specific numbers.'
        elif is_calc:
            instruction = 'Calculate the answer and state it clearly (1-2 sentences). Show the math.'
        else:
            instruction = 'Write a natural response (1-2 sentences). Include specific numbers. No metadata.'

        prompt = f'User asked: "{user_query}"\nData found:\n{context}\n\n{instruction}'
        messages = [
            {"role": "system", "content": "You write brief, accurate data summaries. Include actual numbers."},
            {"role": "user", "content": prompt},
        ]

        max_tok = 150 if is_business else (80 if is_calc else 50)
        return self._call_api(config, messages, temperature=0.3, max_tokens=max_tok,
                               model_override=model_override) or ""

    # ═══════════════════════════════════════════════════════════════
    # Step 3: Response Formatter — AI analyzes raw data for clean output
    # ═══════════════════════════════════════════════════════════════

    FORMATTER_MODEL = 'llama-3.1-8b-instant'
    FORMATTER_PROMPT = """You are a data response formatter for a business assistant. You receive raw data from a database query and must format a clean, user-friendly response.

RULES:
1. Answer the user's question DIRECTLY in the first line. Be specific with numbers.
2. Remove irrelevant or empty data — only show what matters for the question.
3. For count questions: just state the number clearly (e.g., "There are 25 contacts in the system.")
4. For single records: state the key values naturally (e.g., "ERP Implementation has expected revenue of 120,000 and is in Qualified stage.")
5. For lists: If user asked for "all" or "show all", list EVERY record with a numbered list. Do NOT skip or summarize — show all of them. For other list queries, show a summary with key highlights.
6. For calculations (sum/avg/diff): show the calculation and result.
7. For grouping: show the group breakdown with counts.
8. For comparisons: show both sides clearly with the conclusion.
9. If data has duplicates or patterns, point them out.
10. Keep it concise — no filler words, no repeating the question.
11. Use bold (**text**) for key numbers and names.
12. If no records found, say "No data found" and suggest the user check the spelling or try a different query.
13. If a field is empty/missing (marked as [NOT AVAILABLE]), clearly tell the user that specific information is not available. Example: "The phone number for John is not available in the system."
14. Do NOT say "based on the data" or "the query returned" — just give the answer.
15. Do NOT show fields that are empty — only mention they are not available.
16. CRITICAL: When data says "TOTAL X records exist", always say "There are X [items]" using the TOTAL number, NOT the number of rows shown below. The shown rows are just a sample.
17. Do NOT include technical details like model names, field names, or domain filters."""

    @api.model
    def format_response(self, user_query, table_results, model_override=None):
        """
        Step 3: Take raw query results and format a user-friendly response.
        """
        config = self.env['ai.config'].sudo().get_active_config()
        if not config:
            return ""

        provider_type = config.provider
        api_key = config.openai_api_key if provider_type == 'openai' else config.groq_api_key
        if not api_key:
            return ""

        # Build data context from results
        data_parts = []
        for table in table_results:
            label = table.get('label', '')
            rows = table.get('rows', [])
            headers = table.get('headers', [])
            field_keys = table.get('field_keys', [])
            shown = table.get('shown_count', 0)
            total = table.get('total_count', 0)

            if field_keys == ['count']:
                count_val = rows[0][0] if rows else '0'
                data_parts.append(f"[COUNT] {label}: {count_val} (total in database: {total})")
            elif shown == 0:
                data_parts.append(f"[EMPTY] {label}: No records found")
            else:
                # For "top/bottom" queries, use shown count. For "all/show" queries, use total.
                q_low = user_query.lower()
                is_ranked = any(w in q_low for w in ['top ', 'bottom ', 'best ', 'worst ', 'highest', 'lowest'])
                if is_ranked:
                    data_parts.append(f"[DATA] {label}: {shown} records")
                elif total > shown:
                    data_parts.append(f"[DATA] {label}: TOTAL {total} records exist (showing first {shown} below)")
                else:
                    data_parts.append(f"[DATA] {label}: {total} records")
                # Send all rows to formatter when user wants full data
                q_low = user_query.lower()
                show_verbs = ['show ', 'list ', 'display ', 'give me ', 'show me ', 'show the ']
                wants_list = 'all' in q_low or any(q_low.startswith(v) for v in show_verbs)
                max_rows = 50 if wants_list else 10
                for row in rows[:max_rows]:
                    pairs = [f"{h}: {v}" for h, v in zip(headers, row) if v and str(v).strip()]
                    if pairs:
                        data_parts.append(f"  - {' | '.join(pairs)}")

        data_context = "\n".join(data_parts)

        prompt = (
            f'User asked: "{user_query}"\n\n'
            f'Raw data from database:\n{data_context}\n\n'
            f'Format a clean, user-friendly response following the rules. Only include relevant information.'
        )

        messages = [
            {"role": "system", "content": self.FORMATTER_PROMPT},
            {"role": "user", "content": prompt},
        ]

        # Determine token budget based on data size
        total_rows = sum(t.get('shown_count', 0) for t in table_results)
        if total_rows > 20:
            max_tok = 2048  # large list — need space to list all
        elif total_rows > 10:
            max_tok = 1024
        elif total_rows > 3:
            max_tok = 300
        else:
            max_tok = 150

        # Use provider-specific model for formatting
        formatter_model = 'gpt-4o-mini' if config.provider == 'openai' else 'llama-3.1-8b-instant'

        result = self._call_api_single(
            config, messages, temperature=0.3, max_tokens=max_tok,
            json_mode=False, model=formatter_model,
        )

        if result and result != '__RATE_LIMITED__':
            return result.strip()
        return ""

    def test_connection(self, config):
        """Test the connection to the AI provider."""
        provider_type = config.provider
        if provider_type == 'openai':
            api_key = config.openai_api_key
            model = config.openai_model
            url = "https://api.openai.com/v1/chat/completions"
        else:
            api_key = config.groq_api_key
            model = config.groq_model
            url = "https://api.groq.com/openai/v1/chat/completions"

        if not api_key:
            return False, "API Key is missing"

        try:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Ping"}],
                "max_tokens": 5,
            }
            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            resp.raise_for_status()
            return True, f"Connection to {provider_type.title()} successful! Model {model} is responding."
        except Exception as e:
            error_msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_msg = e.response.json().get('error', {}).get('message', error_msg)
                except:
                    pass
            return False, f"Connection failed: {error_msg}"

    # ═══════════════════════════════════════════════════════════════
    # API calls with auto-fallback
    # ═══════════════════════════════════════════════════════════════

    def _call_api(self, config, messages, temperature=0.0, max_tokens=256,
                   json_mode=False, model_override=None):
        """
        Call AI API with automatic model fallback.
        """
        provider_type = config.provider
        if provider_type == 'openai':
            primary_model = model_override or config.openai_model or "gpt-4o"
            # OpenAI doesn't need fallback list as much as Groq free tier
            models_to_try = [primary_model]
        else:
            primary_model = model_override or config.groq_model or "llama-3.3-70b-versatile"
            fallbacks = self.env['ai.config'].sudo().get_fallback_models(primary_model)
            models_to_try = [primary_model] + fallbacks

        for model in models_to_try:
            result = self._call_api_single(config, messages, temperature, max_tokens, json_mode, model)
            if result == '__RATE_LIMITED__':
                _logger.info("Model %s rate limited, trying next...", model)
                continue  # try next model
            return result  # success or non-rate-limit error

        # All models exhausted
        return "__RATE_LIMIT__:All models have reached their limit. Please try again later."

    def _call_api_single(self, config, messages, temperature, max_tokens, json_mode, model):
        """Call AI with a specific model. Returns '__RATE_LIMITED__' on 429."""
        provider_type = config.provider
        if provider_type == 'openai':
            api_key = config.openai_api_key
            url = "https://api.openai.com/v1/chat/completions"
        else:
            api_key = config.groq_api_key
            url = "https://api.groq.com/openai/v1/chat/completions"

        try:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            # Handle JSON mode
            if json_mode:
                if provider_type == 'openai':
                    payload["response_format"] = {"type": "json_object"}
                else:
                    no_json = self.env['ai.config'].NO_JSON_MODE_MODELS
                    if model not in no_json:
                        payload["response_format"] = {"type": "json_object"}

            resp = requests.post(
                url,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=config.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            text = data.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
            _logger.info("%s OK (%d tokens, model: %s)",
                         provider_type.upper(),
                         data.get('usage', {}).get('total_tokens', 0), model)
            return text
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                _logger.warning("%s rate limit on model: %s", provider_type.upper(), model)
                return '__RATE_LIMITED__'
            error_detail = ""
            try:
                error_detail = e.response.json().get('error', {}).get('message', '')
            except Exception:
                pass
            _logger.error("%s API error (%s): %s", provider_type.upper(), model, error_detail or e)
        except requests.ConnectionError:
            _logger.error("Cannot connect to %s API", provider_type.upper())
        except Exception as e:
            _logger.exception("%s error (%s): %s", provider_type.upper(), model, e)
        return None

    # ═══════════════════════════════════════════════════════════════
    # JSON parsing
    # ═══════════════════════════════════════════════════════════════

    def _parse_json_response(self, text):
        """Parse JSON from AI response with multiple fallback strategies."""
        text = text.strip()

        # Strip <think>...</think> tags (Qwen and reasoning models)
        text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()

        # Strategy 1: Direct parse
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 2: Strip markdown code fences
        cleaned = re.sub(r'^```(?:json)?\s*', '', text)
        cleaned = re.sub(r'\s*```$', '', cleaned).strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 3: Extract first complete JSON object
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except (json.JSONDecodeError, ValueError):
                pass

        _logger.warning("Failed to parse AI JSON: %s", text[:300])
        return None
