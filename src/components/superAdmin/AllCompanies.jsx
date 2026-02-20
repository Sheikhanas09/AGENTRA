"use client";

import { useState, useMemo } from "react";

export default function AllCompanies() {
  // Sample company data (replace with API fetch later)
  const [companies, setCompanies] = useState([
    {
      id: 1,
      name: "ABC Corp",
      email: "abc@corp.com",
      status: "Active",
      startDate: "2026-01-01",
      expiryDate: "2026-12-31",
    },
    {
      id: 2,
      name: "XYZ Ltd",
      email: "xyz@ltd.com",
      status: "Pending",
      startDate: "2026-02-01",
      expiryDate: "",
    },
    {
      id: 3,
      name: "Tech Solutions",
      email: "tech@sol.com",
      status: "Rejected",
      startDate: "2025-10-01",
      expiryDate: "",
    },
    {
      id: 4,
      name: "NextGen AI",
      email: "ai@nextgen.com",
      status: "Active",
      startDate: "2026-03-01",
      expiryDate: "2027-03-01",
    },
    // Add more companies as needed
  ]);

  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState("All");
  const [currentPage, setCurrentPage] = useState(1);
  const [sortKey, setSortKey] = useState("name"); // name / status / expiry
  const [sortOrder, setSortOrder] = useState("asc"); // asc / desc
  const itemsPerPage = 5;

  const statusColors = {
    Pending: "bg-yellow-500/20 text-yellow-400",
    Active: "bg-[#05DC7F]/20 text-[#05DC7F]",
    Rejected: "bg-red-500/20 text-red-400",
  };

  // Filter and search
  const filteredCompanies = useMemo(() => {
    return companies
      .filter((c) => {
        const matchesSearch =
          c.name.toLowerCase().includes(search.toLowerCase()) ||
          c.email.toLowerCase().includes(search.toLowerCase());
        const matchesStatus =
          filterStatus === "All" || c.status === filterStatus;
        return matchesSearch && matchesStatus;
      })
      .sort((a, b) => {
        if (sortKey === "expiry") {
          const dateA = a.expiryDate ? new Date(a.expiryDate) : new Date(0);
          const dateB = b.expiryDate ? new Date(b.expiryDate) : new Date(0);
          return sortOrder === "asc" ? dateA - dateB : dateB - dateA;
        } else {
          const valA = a[sortKey].toLowerCase();
          const valB = b[sortKey].toLowerCase();
          if (valA < valB) return sortOrder === "asc" ? -1 : 1;
          if (valA > valB) return sortOrder === "asc" ? 1 : -1;
          return 0;
        }
      });
  }, [companies, search, filterStatus, sortKey, sortOrder]);

  // Pagination
  const totalPages = Math.ceil(filteredCompanies.length / itemsPerPage);
  const paginatedCompanies = filteredCompanies.slice(
    (currentPage - 1) * itemsPerPage,
    currentPage * itemsPerPage,
  );

  const goToPage = (page) => {
    if (page < 1 || page > totalPages) return;
    setCurrentPage(page);
  };

  const calculateRemainingDays = (expiryDate) => {
    if (!expiryDate) return "-";
    const today = new Date();
    const expiry = new Date(expiryDate);
    const diff = Math.ceil((expiry - today) / (1000 * 60 * 60 * 24));
    if (diff < 0) return "Expired";
    if (diff <= 30) return `${diff} days ⚠️`;
    return `${diff} days`;
  };

  // Button handlers
  const handleEdit = (company) => {
    alert(`Edit ${company.name} (replace with modal or redirect)`);
  };

  const handleDeactivate = (id) => {
    setCompanies((prev) =>
      prev.map((c) => (c.id === id ? { ...c, status: "Pending" } : c)),
    );
  };

  const handleDelete = (id) => {
    if (confirm("Are you sure you want to delete this company?")) {
      setCompanies((prev) => prev.filter((c) => c.id !== id));
    }
  };

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortOrder(sortOrder === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortOrder("asc");
    }
  };

  return (
    <div className="text-white/70">
      {/* Search & Filter */}
      <div className="flex flex-col md:flex-row md:justify-between md:items-center gap-3 mb-4">
        <h2 className="text-2xl font-semibold">All Companies</h2>
        <div className="flex flex-wrap gap-3 items-center">
          <input
            type="text"
            placeholder="Search by name or email"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="px-3 py-2 rounded-lg bg-black border border-[#05DC7F]/25 focus:outline-none focus:border-[#05DC7F] transition text-white text-sm"
          />
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="px-3 py-2 rounded-lg bg-black border border-[#05DC7F]/25 focus:outline-none focus:border-[#05DC7F] transition text-white text-sm"
          >
            <option value="All">All Status</option>
            <option value="Active">Active</option>
            <option value="Pending">Pending</option>
            <option value="Rejected">Rejected</option>
          </select>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-[#05DC7F]/25 backdrop-blur-sm shadow-[0_0_10px_rgba(5,220,127,0.25)]">
        <table className="min-w-full divide-y divide-gray-700">
          <thead className="bg-black">
            <tr>
              {["name", "email", "status", "startDate", "expiry"].map((col) => (
                <th
                  key={col}
                  className="px-6 py-3 text-left text-sm font-semibold text-gray-400 uppercase tracking-wider cursor-pointer select-none"
                  onClick={() => handleSort(col)}
                >
                  {col === "name"
                    ? "Company Name"
                    : col === "email"
                      ? "Email"
                      : col === "status"
                        ? "Status"
                        : col === "startDate"
                          ? "Start Date"
                          : "Expiry / Remaining"}{" "}
                  {sortKey === col ? (sortOrder === "asc" ? "▲" : "▼") : ""}
                </th>
              ))}
              <th className="px-6 py-3 text-right text-sm font-semibold text-gray-400 uppercase tracking-wider">
                Actions
              </th>
            </tr>
          </thead>

          <tbody className="divide-y divide-gray-700">
            {paginatedCompanies.map((company) => (
              <tr key={company.id} className="hover:bg-[#05DC7F]/10 transition">
                <td className="px-6 py-4 whitespace-nowrap">{company.name}</td>
                <td className="px-6 py-4 whitespace-nowrap text-gray-400">
                  {company.email}
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  <span
                    className={`px-3 py-1 rounded-full text-sm font-semibold ${statusColors[company.status]}`}
                  >
                    {company.status}
                  </span>
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  {company.startDate}
                </td>
                <td className="px-6 py-4 whitespace-nowrap">
                  {calculateRemainingDays(company.expiryDate)}
                </td>
                <td className="px-6 py-4 whitespace-nowrap text-right flex justify-end gap-2">
                  <button
                    onClick={() => handleEdit(company)}
                    className="px-3 py-1 rounded-lg bg-[#05DC7F] text-black text-sm font-medium hover:bg-green-600 transition"
                  >
                    Edit
                  </button>
                  {company.status === "Active" && (
                    <button
                      onClick={() => handleDeactivate(company.id)}
                      className="px-3 py-1 rounded-lg bg-yellow-500 text-black text-sm font-medium hover:bg-yellow-600 transition"
                    >
                      Deactivate
                    </button>
                  )}
                  <button
                    onClick={() => handleDelete(company.id)}
                    className="px-3 py-1 rounded-lg bg-red-500 text-black text-sm font-medium hover:bg-red-600 transition"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex flex-wrap gap-1 justify-end items-center mt-3 md:mt-4 text-gray-300 text-xs md:text-sm">
        <button
          onClick={() => goToPage(1)}
          className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
        >
          «
        </button>
        <button
          onClick={() => goToPage(currentPage - 1)}
          className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
        >
          ‹
        </button>
        <span className="px-2 py-1 md:px-3">
          Page {currentPage} of {totalPages}
        </span>
        <button
          onClick={() => goToPage(currentPage + 1)}
          className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
        >
          ›
        </button>
        <button
          onClick={() => goToPage(totalPages)}
          className="px-2 py-1 hover:bg-[#05DC7F]/20 rounded"
        >
          »
        </button>
      </div>
    </div>
  );
}
