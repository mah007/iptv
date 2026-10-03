import {
  getCustomersListQueryKey,
  getCustomersRetrieveQueryKey,
  getDashboardKpisQueryKey,
  type CustomerDetail,
} from "@smart-iptv/api";
import type { QueryClient } from "@tanstack/react-query";

/** Lists and KPIs that count customers or devices are stale after any change to one. */
export function invalidateCustomerLists(queryClient: QueryClient): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: getCustomersListQueryKey() }),
    queryClient.invalidateQueries({ queryKey: getDashboardKpisQueryKey() }),
  ]).then(() => undefined);
}

/** A customer changed: refresh their detail page and every list that shows them. */
export function invalidateCustomer(queryClient: QueryClient, id: string): Promise<void> {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: getCustomersRetrieveQueryKey(id) }),
    invalidateCustomerLists(queryClient),
  ]).then(() => undefined);
}

/** The API answered with the updated customer: show it at once, then refresh the lists. */
export function storeCustomer(queryClient: QueryClient, customer: CustomerDetail): Promise<void> {
  queryClient.setQueryData(getCustomersRetrieveQueryKey(customer.id), customer);
  return invalidateCustomerLists(queryClient);
}
