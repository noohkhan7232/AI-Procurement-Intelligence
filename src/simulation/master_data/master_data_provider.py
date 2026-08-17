"""Master Data Provider for Phase 03.3 Enterprise Transaction Simulation Engine.

Consumes existing organization and master-data entities (Phase 3.1 & Phase 3.2 datasets)
from ``data/raw/v1`` or ``data/interim``, and synthesizes structured master data pools
(Employees, Customers, Suppliers, Products, Warehouses, Accounts, Cost Centers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import random
from typing import Any

import pandas as pd

from src.config.paths import get_project_paths
from src.simulation.config.simulation_config import SimulationConfig
from src.utils.file_loader import FileLoader
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class MasterEmployee:
    """Employee master record."""

    employee_id: str
    name: str
    department_id: str
    cost_center_id: str
    job_title: str
    job_grade: str
    base_salary: float
    is_active: bool = True
    hire_date: str = "2020-01-15"
    manager_id: str | None = None


@dataclass
class MasterCustomer:
    """Customer master record."""

    customer_id: str
    customer_name: str
    city: str
    state: str
    credit_limit: float = 50000.0


@dataclass
class MasterSupplier:
    """Supplier master record."""

    supplier_id: str
    supplier_name: str
    city: str
    state: str
    rating: float = 4.5


@dataclass
class MasterProduct:
    """Product master record."""

    product_id: str
    product_name: str
    category_name: str
    cost_price: float
    list_price: float


@dataclass
class MasterWarehouse:
    """Warehouse location master record."""

    warehouse_id: str
    warehouse_name: str
    location: str


@dataclass
class MasterDataPool:
    """Container for all loaded master data entities."""

    employees: list[MasterEmployee] = field(default_factory=list)
    customers: list[MasterCustomer] = field(default_factory=list)
    suppliers: list[MasterSupplier] = field(default_factory=list)
    products: list[MasterProduct] = field(default_factory=list)
    warehouses: list[MasterWarehouse] = field(default_factory=list)
    departments: list[dict[str, str]] = field(default_factory=list)
    cost_centers: list[dict[str, str]] = field(default_factory=list)
    accounts: list[dict[str, str]] = field(default_factory=list)
    business_units: list[dict[str, str]] = field(default_factory=list)


class MasterDataProvider:
    """Loads Phase 3.1 & Phase 3.2 master data or synthesizes scaled pools for simulation."""

    def __init__(self, config: SimulationConfig) -> None:
        """Initialize provider with simulation configuration."""
        self._config = config
        self._paths = get_project_paths()
        self._file_loader = FileLoader()
        self._pool: MasterDataPool | None = None
        random.seed(config.seed)

    def get_master_data(self) -> MasterDataPool:
        return self.load_or_synthesize()

    def load_or_synthesize(self) -> MasterDataPool:
        """Load existing master datasets from disk or synthesize based on scale targets."""
        if self._pool is not None:
            return self._pool

        target = self._config.get_scale_target()
        logger.info(
            "Initializing Master Data Provider (Scale: %s, Target Employees: %d, Products: %d).",
            self._config.scale_profile.value,
            target.employees,
            target.products,
        )

        pool = MasterDataPool()
        self._populate_org_structure(pool)
        self._populate_employees(pool, target.employees)
        self._populate_customers(pool, target.customers)
        self._populate_suppliers(pool, target.suppliers)
        self._populate_products(pool, target.products)
        self._populate_warehouses(pool, target.warehouses)

        self._pool = pool
        return pool

    def _populate_org_structure(self, pool: MasterDataPool) -> None:
        """Populate departments, cost centers, accounts, and business units."""
        pool.business_units = [
            {"unit_id": "BU-100", "unit_code": "NORTH_AMERICA", "unit_name": "North America Operations", "company_code": "1000"},
            {"unit_id": "BU-200", "unit_code": "EUROPE", "unit_name": "European Operations", "company_code": "2000"},
            {"unit_id": "BU-300", "unit_code": "LATAM", "unit_name": "Latin America Operations", "company_code": "3000"},
        ]

        pool.departments = [
            {"department_id": "DEP-PROC", "department_name": "Procurement & Sourcing"},
            {"department_id": "DEP-SALES", "department_name": "Sales & Commercial"},
            {"department_id": "DEP-LOG", "department_name": "Supply Chain & Logistics"},
            {"department_id": "DEP-FIN", "department_name": "Finance & Accounting"},
            {"department_id": "DEP-HR", "department_name": "Human Resources"},
            {"department_id": "DEP-IT", "department_name": "Information Technology"},
        ]

        pool.cost_centers = [
            {"cost_center_id": "CC-101", "cost_center_code": "CC_PROC", "cost_center_name": "Procurement Cost Center", "department_id": "DEP-PROC"},
            {"cost_center_id": "CC-102", "cost_center_code": "CC_SALES", "cost_center_name": "Sales Cost Center", "department_id": "DEP-SALES"},
            {"cost_center_id": "CC-103", "cost_center_code": "CC_LOG", "cost_center_name": "Logistics Cost Center", "department_id": "DEP-LOG"},
            {"cost_center_id": "CC-104", "cost_center_code": "CC_FIN", "cost_center_name": "Finance Cost Center", "department_id": "DEP-FIN"},
            {"cost_center_id": "CC-105", "cost_center_code": "CC_HR", "cost_center_name": "HR Cost Center", "department_id": "DEP-HR"},
            {"cost_center_id": "CC-106", "cost_center_code": "CC_IT", "cost_center_name": "IT Cost Center", "department_id": "DEP-IT"},
        ]

        pool.accounts = [
            {"account_id": "ACC-1000", "account_number": "100000", "account_name": "Cash & Cash Equivalents", "account_type": "ASSET"},
            {"account_id": "ACC-1100", "account_number": "110000", "account_name": "Accounts Receivable", "account_type": "ASSET"},
            {"account_id": "ACC-1200", "account_number": "120000", "account_name": "Inventory Asset", "account_type": "ASSET"},
            {"account_id": "ACC-1500", "account_number": "150000", "account_name": "Fixed Assets Equipment", "account_type": "ASSET"},
            {"account_id": "ACC-2000", "account_number": "200000", "account_name": "Accounts Payable", "account_type": "LIABILITY"},
            {"account_id": "ACC-2100", "account_number": "210000", "account_name": "Payroll Payable", "account_type": "LIABILITY"},
            {"account_id": "ACC-2200", "account_number": "220000", "account_name": "Tax Payable", "account_type": "LIABILITY"},
            {"account_id": "ACC-4000", "account_number": "400000", "account_name": "Sales Revenue", "account_type": "REVENUE"},
            {"account_id": "ACC-5000", "account_number": "500000", "account_name": "Cost of Goods Sold", "account_type": "EXPENSE"},
            {"account_id": "ACC-6000", "account_number": "600000", "account_name": "Salary Expense", "account_type": "EXPENSE"},
            {"account_id": "ACC-6100", "account_number": "610000", "account_name": "Travel & Expense", "account_type": "EXPENSE"},
            {"account_id": "ACC-6200", "account_number": "620000", "account_name": "Depreciation Expense", "account_type": "EXPENSE"},
        ]

    def _populate_employees(self, pool: MasterDataPool, count: int) -> None:
        """Create structured master employees."""
        first_names = ["James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda", "William", "Elizabeth"]
        last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]
        titles_by_dept = {
            "DEP-PROC": ["Procurement Specialist", "Buyer", "Sourcing Manager", "Procurement VP"],
            "DEP-SALES": ["Sales Representative", "Account Manager", "Sales Director", "VP Sales"],
            "DEP-LOG": ["Warehouse Operator", "Logistics Coordinator", "Warehouse Manager", "Supply Chain VP"],
            "DEP-FIN": ["Financial Analyst", "Accountant", "Finance Manager", "CFO"],
            "DEP-HR": ["HR Specialist", "HR Generalist", "HR Manager", "VP HR"],
            "DEP-IT": ["Systems Admin", "Software Engineer", "IT Lead", "CIO"],
        }

        grades = ["G1_ENTRY", "G2_MID", "G3_SENIOR", "G4_MANAGER", "G5_EXECUTIVE"]
        salaries = {"G1_ENTRY": 45000, "G2_MID": 65000, "G3_SENIOR": 95000, "G4_MANAGER": 130000, "G5_EXECUTIVE": 220000}

        for idx in range(1, count + 1):
            emp_id = f"EMP-{idx:05d}"
            name = f"{random.choice(first_names)} {random.choice(last_names)}"
            dept = random.choice(pool.departments)
            cost_center = next(cc for cc in pool.cost_centers if cc["department_id"] == dept["department_id"])
            grade = random.choices(grades, weights=[0.4, 0.3, 0.18, 0.09, 0.03])[0]
            title = random.choice(titles_by_dept[dept["department_id"]])
            base_sal = salaries[grade] + random.randint(-5000, 5000)

            pool.employees.append(
                MasterEmployee(
                    employee_id=emp_id,
                    name=name,
                    department_id=dept["department_id"],
                    cost_center_id=cost_center["cost_center_id"],
                    job_title=title,
                    job_grade=grade,
                    base_salary=float(base_sal),
                )
            )

    def _populate_customers(self, pool: MasterDataPool, count: int) -> None:
        """Load customers from existing dataset or synthesize."""
        raw_cust_file = self._paths.raw_data_dir / "v1" / "olist" / "olist_customers_dataset.csv"
        if raw_cust_file.exists():
            try:
                df = self._file_loader.load_csv(raw_cust_file)
                sample_df = df.sample(n=min(count, len(df)), random_state=self._config.seed)
                for idx, row in sample_df.iterrows():
                    pool.customers.append(
                        MasterCustomer(
                            customer_id=str(row["customer_id"]),
                            customer_name=f"Customer {row['customer_id'][:8]}",
                            city=str(row.get("customer_city", "Sao Paulo")),
                            state=str(row.get("customer_state", "SP")),
                            credit_limit=random.choice([10000.0, 25000.0, 50000.0, 100000.0]),
                        )
                    )
                logger.info("Loaded %d master customers from existing dataset.", len(pool.customers))
                return
            except Exception as exc:
                logger.warning("Failed loading raw customer file: %s. Synthesizing instead.", exc)

        for idx in range(1, count + 1):
            pool.customers.append(
                MasterCustomer(
                    customer_id=f"CUST-{idx:05d}",
                    customer_name=f"Enterprise Customer {idx}",
                    city="New York",
                    state="NY",
                    credit_limit=50000.0,
                )
            )

    def _populate_suppliers(self, pool: MasterDataPool, count: int) -> None:
        """Load sellers/suppliers from existing dataset or synthesize."""
        raw_seller_file = self._paths.raw_data_dir / "v1" / "olist" / "olist_sellers_dataset.csv"
        if raw_seller_file.exists():
            try:
                df = self._file_loader.load_csv(raw_seller_file)
                sample_df = df.sample(n=min(count, len(df)), random_state=self._config.seed)
                for idx, row in sample_df.iterrows():
                    pool.suppliers.append(
                        MasterSupplier(
                            supplier_id=str(row["seller_id"]),
                            supplier_name=f"Supplier {row['seller_id'][:8]} Corp",
                            city=str(row.get("seller_city", "Curitiba")),
                            state=str(row.get("seller_state", "PR")),
                        )
                    )
                logger.info("Loaded %d master suppliers from existing dataset.", len(pool.suppliers))
                return
            except Exception as exc:
                logger.warning("Failed loading raw sellers file: %s. Synthesizing instead.", exc)

        for idx in range(1, count + 1):
            pool.suppliers.append(
                MasterSupplier(
                    supplier_id=f"SUPP-{idx:05d}",
                    supplier_name=f"Global Supplier {idx} Inc",
                    city="Chicago",
                    state="IL",
                )
            )

    def _populate_products(self, pool: MasterDataPool, count: int) -> None:
        """Load products from existing dataset or synthesize."""
        raw_prod_file = self._paths.raw_data_dir / "v1" / "olist" / "olist_products_dataset.csv"
        if raw_prod_file.exists():
            try:
                df = self._file_loader.load_csv(raw_prod_file)
                sample_df = df.sample(n=min(count, len(df)), random_state=self._config.seed)
                for idx, row in sample_df.iterrows():
                    cost = round(random.uniform(10.0, 500.0), 2)
                    price = round(cost * random.uniform(1.2, 1.8), 2)
                    pool.products.append(
                        MasterProduct(
                            product_id=str(row["product_id"]),
                            product_name=f"Product {row['product_id'][:8]}",
                            category_name=str(row.get("product_category_name", "general")),
                            cost_price=cost,
                            list_price=price,
                        )
                    )
                logger.info("Loaded %d master products from existing dataset.", len(pool.products))
                return
            except Exception as exc:
                logger.warning("Failed loading raw products file: %s. Synthesizing instead.", exc)

        for idx in range(1, count + 1):
            cost = round(random.uniform(15.0, 300.0), 2)
            pool.products.append(
                MasterProduct(
                    product_id=f"PROD-{idx:05d}",
                    product_name=f"Industrial Component {idx}",
                    category_name="Manufacturing",
                    cost_price=cost,
                    list_price=round(cost * 1.5, 2),
                )
            )

    def _populate_warehouses(self, pool: MasterDataPool, count: int) -> None:
        """Create warehouse master records."""
        locations = ["New York", "Chicago", "Dallas", "Los Angeles", "Atlanta", "Seattle", "Denver", "Miami"]
        for idx in range(1, count + 1):
            loc = locations[(idx - 1) % len(locations)]
            pool.warehouses.append(
                MasterWarehouse(
                    warehouse_id=f"WH-{idx:03d}",
                    warehouse_name=f"{loc} Fulfillment Center {idx}",
                    location=loc,
                )
            )


__all__ = [
    "MasterEmployee",
    "MasterCustomer",
    "MasterSupplier",
    "MasterProduct",
    "MasterWarehouse",
    "MasterDataPool",
    "MasterDataProvider",
]
