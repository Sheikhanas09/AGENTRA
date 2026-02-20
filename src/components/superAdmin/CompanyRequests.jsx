"use client";

import { useState } from "react";

export default function CompanyRequests() {
  const [companies, setCompanies] = useState([
    { id: 1, name: "ABC Corp", email: "abc@corp.com", status: "Pending" },
    { id: 2, name: "XYZ Ltd", email: "xyz@ltd.com", status: "Pending" },
    { id: 3, name: "Tech Solutions", email: "tech@sol.com", status: "Pending" },
    { id: 4, name: "Global Tech", email: "global@tech.com", status: "Pending" },
    {
      id: 5,
      name: "NextGen Solutions",
      email: "nextgen@sol.com",
      status: "Pending",
    },
    {
      id: 6,
      name: "Innovate Ltd",
      email: "innovate@ltd.com",
      status: "Pending",
    },
    { id: 7, name: "Future Corp", email: "future@corp.com", status: "Pending" },
    { id: 8, name: "Alpha Tech", email: "alpha@tech.com", status: "Pending" },
  ]);

  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 5;

  const handleApprove = (id) => {
    setCompanies((prev) =>
      prev.map((c) => (c.id === id ? { ...c, status: "Active" } : c)),
    );
  };

  const handleReject = (id) => {
    setCompanies((prev) =>
      prev.map((c) => (c.id === id ? { ...c, status: "Rejected" } : c)),
    );
  };

  const statusColors = {
    Pending: "bg-yellow-500/20 text-yellow-400",
    Active: "bg-[#05DC7F]/20 text-[#05DC7F]",
    Rejected: "bg-red-500/20 text-red-400",
  };

  // Pagination calculations
  const totalPages = Math.ceil(companies.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentCompanies = companies.slice(
    startIndex,
    startIndex + itemsPerPage,
  );

  const goToPage = (page) => {
    if (page < 1) return setCurrentPage(1);
    if (page > totalPages) return setCurrentPage(totalPages);
    setCurrentPage(page);
  };

  return (
    <div className="text-white/70">
      <h2 className="text-2xl font-semibold mb-4">
        Company Registration Requests
      </h2>

      <div className="overflow-x-auto rounded-xl border border-[#05DC7F]/25 backdrop-blur-sm shadow-[0_0_10px_rgba(5,220,127,0.25)]">
        <table className="min-w-full divide-y divide-gray-700">
          <thead className="bg-[#0A0F18]">
            <tr>
              <th className="px-6 py-3 text-left text-sm font-semibold text-gray-400 uppercase tracking-wider">
                Company Name
              </th>
              <th className="px-6 py-3 text-left text-sm font-semibold text-gray-400 uppercase tracking-wider">
                Email
              </th>
              <th className="px-6 py-3 text-left text-sm font-semibold text-gray-400 uppercase tracking-wider">
                Status
              </th>
              <th className="px-6 py-3 text-right text-sm font-semibold text-gray-400 uppercase tracking-wider">
                Actions
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-700">
            {currentCompanies.map((company) => (
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
                <td className="px-6 py-4 whitespace-nowrap text-right">
                  {company.status === "Pending" && (
                    <div className="flex justify-end gap-2">
                      <button
                        onClick={() => handleApprove(company.id)}
                        className="px-3 py-1 rounded-lg bg-[#05DC7F] text-black text-sm font-medium hover:bg-green-600 transition"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => handleReject(company.id)}
                        className="px-3 py-1 rounded-lg bg-red-500 text-black text-sm font-medium hover:bg-red-600 transition"
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination Footer */}
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
