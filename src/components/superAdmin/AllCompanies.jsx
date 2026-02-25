import { useState } from "react";

export default function AllCompanies() {
  const [activeTab, setActiveTab] = useState("Active");

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
      status: "Inactive",
      startDate: "2026-02-01",
      expiryDate: "2026-08-01",
    },
    {
      id: 3,
      name: "Tech Solutions",
      email: "tech@sol.com",
      status: "Pending",
    },
  ]);

  const [currentPage, setCurrentPage] = useState(1);
  const itemsPerPage = 5;

  const statusColors = {
    Pending: "bg-yellow-500/20 text-yellow-400",
    Active: "bg-[#05DC7F]/20 text-[#05DC7F]",
    Inactive: "bg-gray-500/20 text-gray-400",
    Rejected: "bg-red-500/20 text-red-400",
  };

  // Filter data based on active tab
  const filteredCompanies = companies.filter((c) =>
    activeTab === "Requests" ? c.status === "Pending" : c.status === activeTab,
  );

  const totalPages = Math.ceil(filteredCompanies.length / itemsPerPage);
  const startIndex = (currentPage - 1) * itemsPerPage;
  const currentCompanies = filteredCompanies.slice(
    startIndex,
    startIndex + itemsPerPage,
  );

  const goToPage = (page) => {
    if (page < 1) return setCurrentPage(1);
    if (page > totalPages) return setCurrentPage(totalPages);
    setCurrentPage(page);
  };

  const toggleStatus = (id) => {
    setCompanies((prev) =>
      prev.map((c) =>
        c.id === id
          ? { ...c, status: c.status === "Active" ? "Inactive" : "Active" }
          : c,
      ),
    );
  };

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

  return (
    <div className="text-white/70">
      {/* Tabs */}
      <div className="flex gap-4 mb-6 border-b border-[#05DC7F]/25 pb-2">
        {["Active", "Inactive", "Requests"].map((tab) => (
          <button
            key={tab}
            onClick={() => {
              setActiveTab(tab);
              setCurrentPage(1);
            }}
            className={`px-4 py-2 rounded-t-lg transition ${
              activeTab === tab
                ? "bg-[#05DC7F] text-black font-semibold"
                : "hover:bg-[#05DC7F]/20"
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-xl border border-[#05DC7F]/25 backdrop-blur-sm shadow-[0_0_10px_rgba(5,220,127,0.25)]">
        <table className="min-w-full divide-y divide-gray-700">
          <thead className="bg-[#0A0F18]">
            <tr>
              <th className="px-6 py-3 text-left text-sm font-semibold text-gray-400 uppercase">
                Company Name
              </th>
              <th className="px-6 py-3 text-left text-sm font-semibold text-gray-400 uppercase">
                Email
              </th>
              <th className="px-6 py-3 text-left text-sm font-semibold text-gray-400 uppercase">
                Status
              </th>
              {activeTab !== "Requests" && (
                <>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-gray-400 uppercase">
                    Start Date
                  </th>
                  <th className="px-6 py-3 text-left text-sm font-semibold text-gray-400 uppercase">
                    Expiry Date
                  </th>
                </>
              )}
              <th className="px-6 py-3 text-right text-sm font-semibold text-gray-400 uppercase">
                Actions
              </th>
            </tr>
          </thead>

          <tbody className="divide-y divide-gray-700">
            {currentCompanies.map((company) => (
              <tr key={company.id} className="hover:bg-[#05DC7F]/10 transition">
                <td className="px-6 py-4">{company.name}</td>
                <td className="px-6 py-4 text-gray-400">{company.email}</td>
                <td className="px-6 py-4">
                  <span
                    className={`px-3 py-1 rounded-full text-sm font-semibold ${statusColors[company.status]}`}
                  >
                    {company.status}
                  </span>
                </td>

                {activeTab !== "Requests" && (
                  <>
                    <td className="px-6 py-4">{company.startDate || "-"}</td>
                    <td className="px-6 py-4">{company.expiryDate || "-"}</td>
                  </>
                )}

                <td className="px-6 py-4 text-right">
                  {activeTab === "Requests" ? (
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
                  ) : (
                    <button
                      onClick={() => toggleStatus(company.id)}
                      className="px-3 py-1 rounded-lg bg-yellow-500 text-black text-sm font-medium hover:bg-yellow-600 transition"
                    >
                      {company.status === "Active"
                        ? "Set Inactive"
                        : "Set Active"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      <div className="flex flex-wrap gap-1 justify-end items-center mt-3 text-gray-300 text-xs md:text-sm">
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
        <span className="px-3">
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
