from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from portal.models import Chapter, Cluster, Criterion


class Command(BaseCommand):
    help = "Populează (sau actualizează) clusterele, capitolele și criteriile de bază (în limba română)."

    @transaction.atomic
    def handle(self, *args, **options):
        # Clustere (modelul UE de negociere pe clustere)
        clusters = [
            (1, "Valori fundamentale", "bi-shield-check", 10),
            (2, "Piața internă", "bi-shop", 20),
            (3, "Competitivitate și creștere incluzivă", "bi-graph-up-arrow", 30),
            (4, "Agenda verde și conectivitate durabilă", "bi-leaf", 40),
            (5, "Resurse, agricultură și coeziune", "bi-geo", 50),
            (6, "Relații externe", "bi-globe", 60),
        ]

        cluster_map: dict[int, Cluster] = {}
        for cod, denumire, pictograma, ordonare in clusters:
            obj, _ = Cluster.objects.update_or_create(
                cod=cod,
                defaults={
                    "denumire": denumire,
                    "pictograma": pictograma,
                    "ordonare": ordonare,
                },
            )
            cluster_map[cod] = obj

        # Criterii (pe lângă capitole)
        criterii = [
            ("FID", "Funcționarea instituțiilor democratice", "bi-building"),
            ("RAP", "Reforma administrației publice", "bi-diagram-3"),
            ("ECON", "Criterii economice", "bi-bar-chart-line"),
        ]
        for cod, denumire, pictograma in criterii:
            Criterion.objects.update_or_create(
                cod=cod,
                defaults={
                    "denumire": denumire,
                    "pictograma": pictograma,
                },
            )

        # Capitole (35)
        # Mapping capitol -> cluster (34/35 rămân fără cluster)
        chapters = [
            (1, 2, "Libera circulație a mărfurilor", "bi-box-seam"),
            (2, 2, "Libera circulație a lucrătorilor", "bi-people"),
            (3, 2, "Dreptul de stabilire și libera prestare a serviciilor", "bi-briefcase"),
            (4, 2, "Libera circulație a capitalului", "bi-currency-euro"),
            (5, 1, "Achiziții publice", "bi-bag-check"),
            (6, 2, "Dreptul societăților comerciale", "bi-building-gear"),
            (7, 2, "Dreptul de proprietate intelectuală", "bi-lightbulb"),
            (8, 2, "Politica în domeniul concurenței", "bi-trophy"),
            (9, 2, "Servicii financiare", "bi-bank"),
            (10, 3, "Societatea informațională și mass-media", "bi-broadcast"),
            (11, 5, "Agricultura și dezvoltarea rurală", "bi-tree"),
            (12, 5, "Siguranța alimentară, politici sanitare și fitosanitare", "bi-shield-plus"),
            (13, 5, "Pescuit", "bi-water"),
            (14, 4, "Politica de transport", "bi-truck"),
            (15, 4, "Energie", "bi-lightning-charge"),
            (16, 3, "Fiscalitate", "bi-receipt"),
            (17, 3, "Politica economică și monetară", "bi-cash-coin"),
            (18, 1, "Statistici", "bi-bar-chart"),
            (19, 3, "Politica socială și ocuparea forței de muncă", "bi-clipboard2-heart"),
            (20, 3, "Politica industrială și antreprenoriat", "bi-building-up"),
            (21, 4, "Rețele transeuropene", "bi-diagram-3"),
            (22, 5, "Politica regională și coordonarea instrumentelor structurale", "bi-map"),
            (23, 1, "Sistem judiciar și drepturi fundamentale", "bi-balance-scale"),
            (24, 1, "Justiție, libertate și securitate", "bi-shield-lock"),
            (25, 3, "Știință și cercetare", "bi-mortarboard"),
            (26, 3, "Educație și cultură", "bi-book"),
            (27, 4, "Mediu și schimbări climatice", "bi-cloud-sun"),
            (28, 2, "Protecția consumatorului și a sănătății", "bi-heart-pulse"),
            (29, 3, "Uniunea vamală", "bi-boxes"),
            (30, 6, "Relații externe", "bi-people-fill"),
            (31, 6, "Politica externă, de securitate și de apărare", "bi-shield"),
            (32, 1, "Control financiar", "bi-clipboard-check"),
            (33, 5, "Dispoziții financiare și bugetare", "bi-piggy-bank"),
            (34, None, "Instituții", "bi-building"),
            (35, None, "Alte aspecte", "bi-three-dots"),
        ]

        for numar, cluster_cod, denumire, pictograma in chapters:
            cluster_obj = cluster_map.get(cluster_cod) if cluster_cod else None
            Chapter.objects.update_or_create(
                numar=numar,
                defaults={
                    "denumire": denumire,
                    "cluster": cluster_obj,
                    "pictograma": pictograma,
                },
            )

        self.stdout.write(self.style.SUCCESS("Referințele au fost populate/actualizate cu succes."))
