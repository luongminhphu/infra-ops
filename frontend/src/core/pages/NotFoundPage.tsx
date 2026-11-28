import { Link } from "react-router-dom";
import { MapPinOff } from "lucide-react";
import { EmptyState } from "@/core/ui/feedback";

export function NotFoundPage() {
  return (
    <EmptyState icon={MapPinOff} title="Không tìm thấy trang" action={<Link to="/" className="text-primary underline">Về Trang chủ</Link>}>
      Đường dẫn có thể đã thay đổi hoặc module chưa được bật.
    </EmptyState>
  );
}
