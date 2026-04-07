import { ReactNode } from 'react';
import { Inbox } from 'lucide-react';

interface Props {
    icon?: ReactNode;
    title: string;
    description: string;
    action?: ReactNode;
}

export default function EmptyState({ icon, title, description, action }: Props) {
    return (
        <div className="text-center py-12">
            <div className="flex justify-center mb-4 text-gray-400">
                {icon || <Inbox className="h-12 w-12" />}
            </div>
            <h3 className="text-sm font-medium text-gray-900">{title}</h3>
            <p className="mt-1 text-sm text-gray-500">{description}</p>
            {action && <div className="mt-6">{action}</div>}
        </div>
    );
}