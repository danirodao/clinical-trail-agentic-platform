interface Props {
    status: string;
}

const statusStyles: Record<string, string> = {
    pending: 'bg-yellow-100 text-yellow-800',
    approved: 'bg-green-100 text-green-800',
    rejected: 'bg-red-100 text-red-800',
    revoked: 'bg-gray-100 text-gray-800',
    active: 'bg-green-100 text-green-800',
    expired: 'bg-gray-100 text-gray-500',
    public: 'bg-blue-100 text-blue-800',
    standard: 'bg-gray-100 text-gray-800',
    sensitive: 'bg-orange-100 text-orange-800',
    restricted: 'bg-red-100 text-red-800',
};

export default function StatusBadge({ status }: Props) {
    return (
        <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${statusStyles[status] || 'bg-gray-100 text-gray-600'
            }`}>
            {status}
        </span>
    );
}